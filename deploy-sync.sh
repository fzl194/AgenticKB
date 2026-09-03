#!/bin/bash
# 增量同步：外网 pack → 传输 → 内网 apply。不重建容器、不触碰内网配置。
#
# 设计依据：docs/部署同步机制-配置审计与方案-2026-09-01.md
#
# 用法：
#   bash deploy-sync.sh pack                    # 外网：打增量包（产物 + Python 源码）。
#                                                #   会先提醒版本决策：是否升级 releases.json
#   bash deploy-sync.sh pack --code-only        # 外网：只打 Python 源码（jar/dist 未变时，包最小）
#   bash deploy-sync.sh apply <sync-*.tar.gz>   # 内网：校验、同 inode 内容同步、按需重启服务
#
# 压缩格式用 gzip（非 zstd）：外网 Windows 开发机通常没有 zstd，
# gzip 两端必有；十几 MB 级别的包压缩率差异可忽略。
#
# 两条铁律（与 deploy-server.sh 一致）：
#   1. 永不触碰 main_control_service/config/（内外网配置差异所在）
#   2. 永不触碰 .env（内网本地生成的密钥文件）
#
# 何时不能用本脚本（需走 deploy-build.sh 全量镜像）：
#   pyproject.toml / pom.xml / package.json 依赖变更；Dockerfile / nginx.conf /
#   supervisord.conf 变更。pack 会检测并提醒。

set -Eeuo pipefail

# Git Bash（MSYS）会把 docker exec 的容器绝对路径改写成宿主机路径。
export MSYS_NO_PATHCONV=1

# Windows 下从受限 PATH 启动时（Claude Code 等），补全 Git 自带 coreutils。
# 判定方式不依赖 uname 本身：MSYS 特有环境变量存在即补（Linux 上不存在）。
if [ -n "${MSYSTEM:-}" ] || [ -n "${MSYS:-}" ]; then
    export PATH="/usr/bin:$PATH"
fi

# ── 同步面定义 ──────────────────────────────────────────────
# 目录名:restart的服务（逗号分隔，按依赖顺序展开；"-" 表示无需重启）
# main_control_service 的 config/ 在 pack/apply 两侧都被排除。
SYNC_DIRS=(
    "knowledge_mining:mining"
    "llm_service:llm_service"
    "main_control_service:control,llm_service,mining,serving,mcp"
    "mcp_server:mcp"
    "databases:-"
    "kb-ui-dist:-"
    "runtime:serving"
)
DEPENDENCY_MANIFESTS="pyproject.toml agent_serving_java/pom.xml kb-ui/package.json"
# 依赖清单基线：pack 成功后自动落盘，下次 pack 对比——有变更则提醒走全量镜像。
# （环境变量 SYNC_LAST_MANIFEST_DIR 可覆盖位置；目录已 gitignore。）
MANIFEST_BASELINE_DIR="${SYNC_LAST_MANIFEST_DIR:-.cmkb-sync-last}"
# 发布清单（版本号）：pack 带上，apply 变更时 restart control（主控启动时读它上报版本）。
RELEASE_MANIFEST="releases.json"
APP_CONTAINER_NAME="${APP_CONTAINER_NAME:-cmkb}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-cmkb}"

die() { echo "错误：$*" >&2; exit 1; }

compose() {
    if docker compose version >/dev/null 2>&1; then
        docker compose -p "$COMPOSE_PROJECT_NAME" "$@"
    else
        COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" docker-compose "$@"
    fi
}

# ── pack：外网执行 ─────────────────────────────────────────
PACK_STAGE=""

cleanup_pack() {
    [ -n "$PACK_STAGE" ] && [ -d "$PACK_STAGE" ] && rm -rf "$PACK_STAGE"
}
trap cleanup_pack EXIT

# 读 releases.json 的当前版本（pack/apply 共用；文件由本体系生成，格式固定）
read_release_version() {
    [ -f "$1" ] || { echo ""; return 0; }
    sed -n 's/^[[:space:]]*"current":[[:space:]]*"\([^"]*\)".*/\1/p' "$1" | head -n 1
}

# 版本决策（用户要求：每次同步前提醒）。升级则写入 releases.json：
# current 指针 + releases 数组头部插入新条目（前端弹窗与 /health 版本即随包走）。
update_release_manifest() {
    local new_version="$1" note="$2"
    python - "$RELEASE_MANIFEST" "$new_version" "$note" <<'PY'
import datetime
import json
import sys

path, version, note = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, encoding="utf-8") as f:
    data = json.load(f)
if any(rel.get("version") == version for rel in data.get("releases", [])):
    sys.exit(f"版本 {version} 已存在于 releases.json，请换一个版本号")
data["current"] = version
data.setdefault("releases", []).insert(0, {
    "version": version,
    "released_at": datetime.date.today().isoformat(),
    "title": (note or f"版本 {version} 增量更新")[:60],
    "changes": [note] if note else [f"版本 {version} 增量更新"],
})
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
}

remind_version_decision() {
    local current answer new_version note
    current="$(read_release_version "$RELEASE_MANIFEST")"
    echo "=== 版本决策 ==="
    echo "当前发布版本：${current:-<无 releases.json>}"
    if [ ! -t 0 ] && [ "${SYNC_FORCE_VERSION_ASK:-0}" != "1" ]; then
        echo "（非交互环境：跳过版本决策，沿用 $current。"
        echo "  如需升级版本号，请手动编辑 releases.json 后重新 pack，"
        echo "  或设 SYNC_FORCE_VERSION_ASK=1 强制交互。）"
        return 0
    fi
    read -r -p "本次同步是否升级版本号？[y/N] " answer
    case "$answer" in
        y|Y) ;;
        *)
            echo "沿用 $current（releases.json 无变化，apply 不触发因版本号的重启）。"
            return 0
            ;;
    esac
    while :; do
        read -r -p "新版本号（如 1.0.1）： " new_version
        if [ -z "$new_version" ] || [ "$new_version" = "$current" ]; then
            echo "版本号为空或与当前相同，请重输。"
            continue
        fi
        break
    done
    read -r -p "一句话更新说明（回车用默认）： " note
    update_release_manifest "$new_version" "$note" || die "写入 releases.json 失败"
    echo "已升级版本：$current -> $new_version（将随本次增量包生效）"
}

cmd_pack() {
    local code_only=false
    [ "${1:-}" = "--code-only" ] && code_only=true

    local stamp archive
    stamp="$(date +%Y%m%d-%H%M%S)"
    archive="sync-${stamp}.tar.gz"

    # 前置校验：构建产物存在性（--code-only 跳过）
    local jar=""
    if [ "$code_only" = false ]; then
        jar="$(ls -t agent_serving_java/target/agent-serving-*.jar 2>/dev/null | head -1 || true)"
        [ -n "$jar" ] \
            || die "未找到 jar。先执行：cd agent_serving_java && mvn -DskipTests package（或用 --code-only 只同步 Python）"
        [ -d kb-ui/dist ] \
            || die "未找到 kb-ui/dist。先执行：cd kb-ui && npm run build（或用 --code-only 只同步 Python）"
    fi

    # 版本决策：必须在打包前完成（releases.json 要进包）
    remind_version_decision

    # 依赖清单变更检测：与上次成功 pack 的基线对比（增量包不含依赖安装，
    # 变更了必须走全量镜像）。基线在 pack 成功后落盘。
    local manifest_changed=false
    for manifest in $DEPENDENCY_MANIFESTS; do
        if [ -f "${MANIFEST_BASELINE_DIR}/${manifest}" ] \
            && ! cmp -s "$manifest" "${MANIFEST_BASELINE_DIR}/${manifest}"; then
            echo "警告：$manifest 相比上次同步有变更。依赖变更不在增量包能力内，" >&2
            echo "请走 deploy-build.sh 全量镜像，否则内网容器缺依赖。" >&2
            manifest_changed=true
        fi
    done

    # 先落盘暂存再打包：不用 tar --transform（其对参数位置的语义不可靠，
    # 实测会把后续目录内容拍扁）。暂存目录结构 = apply 侧期望的最终结构。
    PACK_STAGE="$(mktemp -d "${PWD}/.cmkb-pack-stage.XXXXXX")"
    echo "=== 正在暂存同步内容 ==="
    local entry dir
    for entry in "${SYNC_DIRS[@]}"; do
        dir="${entry%%:*}"
        case "$dir" in
            runtime)
                [ "$code_only" = true ] && continue
                mkdir -p "$PACK_STAGE/runtime"
                cp -- "$jar" "$PACK_STAGE/runtime/agent_serving.jar"
                ;;
            kb-ui-dist)
                [ "$code_only" = true ] && continue
                cp -a kb-ui/dist "$PACK_STAGE/kb-ui-dist"
                ;;
            *)
                cp -a "$dir" "$PACK_STAGE/$dir"
                ;;
        esac
    done
    # 铁律：包内绝不携带内网配置与密钥文件（apply 侧还有防御性复查）
    rm -rf "$PACK_STAGE/main_control_service/config"
    find "$PACK_STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
    find "$PACK_STAGE" -name '*.pyc' -delete
    find "$PACK_STAGE" -name '.pytest_cache' -type d -prune -exec rm -rf {} +

    # 打包前自检：暂存区不得残留 config/ 与 .env
    if [ -e "$PACK_STAGE/main_control_service/config" ] \
        || find "$PACK_STAGE" -name '.env' | grep -q .; then
        die "暂存区包含 config/ 或 .env，内部错误，中止"
    fi

    # 发布清单：随包携带（apply 侧变更检测，变了 restart control）
    [ -f "$RELEASE_MANIFEST" ] && cp -- "$RELEASE_MANIFEST" "$PACK_STAGE/$RELEASE_MANIFEST"

    echo "=== 正在打包 ==="
    tar -czf "$archive" -C "$PACK_STAGE" .
    sha256sum "$archive" > "${archive}.sha256"

    echo "=== 增量包生成完成 ==="
    ls -lh "$archive" "${archive}.sha256"

    # 依赖清单基线落盘（下次 pack 对比用；目录 gitignore）。
    # cp --parents：pom.xml 这类嵌套路径的父目录自动补齐。
    mkdir -p "$MANIFEST_BASELINE_DIR"
    for manifest in $DEPENDENCY_MANIFESTS; do
        [ -f "$manifest" ] && cp --parents -- "$manifest" "$MANIFEST_BASELINE_DIR/"
    done

    echo "请将两个文件一起传到内网服务器仓库根目录，然后执行："
    echo "  bash deploy-sync.sh apply $archive"
}

# ── apply：内网执行 ────────────────────────────────────────
STAGE_DIR=""
SWAP_LOG=""   # 已交换目录清单（回滚用）

cleanup_apply() {
    [ -n "$STAGE_DIR" ] && [ -d "$STAGE_DIR" ] && rm -rf "$STAGE_DIR"
    [ -n "$SWAP_LOG" ] && [ -f "$SWAP_LOG" ] && rm -f "$SWAP_LOG"
}
trap cleanup_apply EXIT

# 同 inode 内容同步：清空目标目录的子项再拷入，目录本身绝不 rename/重建。
#
# 为什么不能 rename 换目录（原实现的 bug）：这些目录都是 docker-compose 的
# bind-mount 源，Linux bind-mount 锁定的是 inode/dentry——宿主机把目录 rename
# 走后容器内挂载仍然指向旧树（实测：Docker VM 内 rename-swap 后容器读到的
# 还是旧文件），随后删除旧目录等于把容器内路径掏空，服务重启即读不到代码。
# Windows 上 rename 直接报错（部署指南附 1 的坑），两平台统一走"删内容+拷入"。
#
# 取舍：目录内容替换的瞬间（秒级）非原子，恰逢服务自行崩溃重启才会读到
# 半新半旧；服务重启只发生在全部目录同步完成之后，正常路径无此窗口。
# 回滚依赖 pack 全量重放；服务重启失败可单独重试（supervisorctl 幂等）。
sync_dir_contents() {
    local dir="$1"

    mkdir -p "$dir"
    if [ "$dir" = "main_control_service" ]; then
        # 铁律：内网 config/ 原样保留（包内也没有 config，见 pack 侧排除）
        find "$dir" -mindepth 1 -maxdepth 1 ! -name config -exec rm -rf {} +
    else
        find "$dir" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    fi
    cp -a "$STAGE_DIR/$dir/." "$dir/"

    # SELinux enforcing 时宿主机 cp 的新文件不会自动带上 :Z 标签（disabled
    # 时 selinuxenabled 不可用/返回非 0，跳过）。nginx 读不到新前端就是这个坑。
    if command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled; then
        chcon -R -t container_file_t "$dir" 2>/dev/null || true
    fi

    echo "$dir" >> "$SWAP_LOG"
}

# 计算目录内容签名（变更检测用）：文件路径 + 相对 mtime/size 的排序列表
dir_signature() {
    (cd "$1" && find . -type f -not -path '*/__pycache__/*' -not -name '*.pyc' \
        -not -path '*/.pytest_cache/*' -printf '%p %s %T@\n' | sort)
}

# 依赖顺序的全量服务重启序列
ALL_SERVICES_IN_ORDER="control llm_service mining serving mcp"

restart_services() {
    local services="$1"
    local svc
    echo "=== 正在重启服务：$services ==="
    for svc in $ALL_SERVICES_IN_ORDER; do
        case " $services " in
            *" $svc "*)
                echo "--- supervisorctl restart $svc"
                compose exec -T app supervisorctl restart "$svc" \
                    || die "无法重启 $svc。目录已更新；请排查后手动 supervisorctl restart $svc"
                ;;
        esac
    done
}

health_check() {
    local url="$1" name="$2" deadline=$((SECONDS + 60))
    echo "等待 $name：$url"
    until compose exec -T app curl -fsS --max-time 3 "$url" >/dev/null 2>&1; do
        if [ "$SECONDS" -ge "$deadline" ]; then
            die "$name 在 60 秒内未恢复健康。目录已更新；请查 ./logs/ 排查后手动重启"
        fi
        sleep 2
    done
    echo "正常：$name"
}

verify_health_by_services() {
    local services="$1"
    case " $services " in
        *" control "*)    health_check "http://127.0.0.1:8910/health" "control" ;;
    esac
    case " $services " in
        *" llm_service "*) health_check "http://127.0.0.1:8900/health" "llm_service" ;;
    esac
    case " $services " in
        *" mining "*)     health_check "http://127.0.0.1:8901/health" "mining" ;;
    esac
    case " $services " in
        *" serving "*)    health_check "http://127.0.0.1:8081/actuator/health" "serving" ;;
    esac
    case " $services " in
        # mcp 对裸 GET 回 405/400 属正常——任意 HTTP 回包即算在线（与 deploy-server 一致）
        *" mcp "*)        http_responds "http://127.0.0.1:9000/mcp" "mcp_server" ;;
    esac
}

http_responds() {
    local url="$1" name="$2" deadline=$((SECONDS + 60))
    echo "等待 $name 接受 HTTP 请求：$url"
    until compose exec -T app curl -sS --max-time 3 -o /dev/null "$url" >/dev/null 2>&1; do
        if [ "$SECONDS" -ge "$deadline" ]; then
            die "$name 在 60 秒内未能接受 HTTP 请求。目录已更新；请查 ./logs/ 排查"
        fi
        sleep 2
    done
    echo "正常：$name"
}

cmd_apply() {
    local archive="${1:-}"
    [ -n "$archive" ] && [ -f "$archive" ] || die "用法：bash deploy-sync.sh apply <sync-*.tar.gz>（在仓库根目录执行）"

    # 1. 校验完整性
    if [ -f "${archive}.sha256" ]; then
        echo "=== 校验包完整性 ==="
        sha256sum -c "${archive}.sha256" || die "校验失败，请重新传输"
    else
        echo "警告：未找到 ${archive}.sha256，跳过完整性校验。" >&2
    fi

    # 2. 解包到暂存区
    STAGE_DIR="$(mktemp -d "${PWD}/.cmkb-sync-stage.XXXXXX")"
    SWAP_LOG="$(mktemp "${PWD}/.cmkb-sync-swap.XXXXXX")"
    : > "$SWAP_LOG"
    echo "=== 解包到暂存区 ==="
    tar -xzf "$archive" -C "$STAGE_DIR"

    # 安全检查：包内禁止出现 config 与 .env（pack 侧已排除，防御性再验）
    if [ -e "$STAGE_DIR/main_control_service/config" ]; then
        die "包内含 main_control_service/config/，拒绝应用（防内网配置被覆盖）"
    fi
    if find "$STAGE_DIR" -name '.env' | grep -q .; then
        die "包内含 .env 文件，拒绝应用"
    fi

    # 3. 逐目录：变更检测 → 原子交换；汇总需重启的服务
    local entry dir services needed="" sig_file
    for entry in "${SYNC_DIRS[@]}"; do
        dir="${entry%%:*}"
        services="${entry#*:}"
        [ -d "$STAGE_DIR/$dir" ] || continue

        if [ "$services" = "-" ] || [ -z "$services" ]; then
            # 无服务关联（databases/kb-ui-dist）：直接同步，nginx 静态文件即时生效
            sync_dir_contents "$dir"
            echo "已更新：$dir（无需重启）"
            continue
        fi

        # 变更检测：内容签名一致则跳过（省一次无谓重启）
        sig_file="$STAGE_DIR/.sig-$dir"
        dir_signature "$STAGE_DIR/$dir" > "$sig_file"
        if [ -d "$dir" ] && dir_signature "$dir" | cmp -s - "$sig_file"; then
            echo "无变化：$dir（跳过）"
            rm -rf "$STAGE_DIR/$dir"
            continue
        fi

        sync_dir_contents "$dir"
        # 逗号列表（main_control_service 的级联）展开成空格分隔——
        # 否则 restart_services 的单服务名匹配一个都命中不了（实测踩过）。
        needed="$needed ${services//,/ }"
        echo "已更新：$dir"
    done

    # 发布清单：变了才覆盖 + 计入 control 重启。
    # 同 inode 截断写（cat > 目标）：单文件 bind-mount 挂的是 inode，
    # mv/rm 换文件会让容器内挂载与磁盘脱钩——绝不能换文件。
    if [ -f "$STAGE_DIR/$RELEASE_MANIFEST" ]; then
        if [ ! -f "$RELEASE_MANIFEST" ] || ! cmp -s "$STAGE_DIR/$RELEASE_MANIFEST" "$RELEASE_MANIFEST"; then
            local old_version new_version
            old_version="$(read_release_version "$RELEASE_MANIFEST")"
            new_version="$(read_release_version "$STAGE_DIR/$RELEASE_MANIFEST")"
            if [ -f "$RELEASE_MANIFEST" ]; then
                cat -- "$STAGE_DIR/$RELEASE_MANIFEST" > "$RELEASE_MANIFEST"
            else
                # 宿主机还没有（未走过镜像补齐的异常态）：直接落文件
                cp -- "$STAGE_DIR/$RELEASE_MANIFEST" "$RELEASE_MANIFEST"
            fi
            case " $needed " in
                *" control "*) ;;
                *) needed="$needed control" ;;
            esac
            echo "已更新：$RELEASE_MANIFEST（版本 ${old_version:-<无>} -> ${new_version:-<无>}，restart control 生效）"
        else
            echo "无变化：$RELEASE_MANIFEST（版本 $(read_release_version "$RELEASE_MANIFEST")）"
        fi
    fi

    # 4. 按依赖顺序重启受影响服务 + 健康检查
    needed="$(printf '%s' "$needed" | tr ' ' '\n' | sort -u | tr '\n' ' ')"
    if [ -z "$(printf '%s' "$needed" | tr -d ' ')" ]; then
        echo "=== 所有目录均无变化，无需重启 ==="
        return 0
    fi
    restart_services "$needed"
    verify_health_by_services "$needed"
    echo "=== 同步完成 ==="
    compose exec -T app supervisorctl status
}

# ── 入口 ───────────────────────────────────────────────────
case "${1:-}" in
    pack)  shift; cmd_pack "$@" ;;
    apply) shift; cmd_apply "$@" ;;
    *)
        sed -n '2,15p' "$0"
        exit 1
        ;;
esac
