#!/bin/bash
# 服务器端部署脚本。
# 用法：
#   bash deploy-server.sh                 # 加载镜像，保留已有代码和配置
#   bash deploy-server.sh --force         # 加载镜像并覆盖代码，保留宿主机配置
#   bash deploy-server.sh --force-config  # 用镜像覆盖主控服务配置
#   bash deploy-server.sh --apply-config  # 保留现有文件，重建容器并校验配置

set -Eeuo pipefail

# Git Bash（MSYS）会把 docker exec 的容器绝对路径改写成宿主机路径，
# 导致配置校验误判——部署脚本对路径转换自防护。
export MSYS_NO_PATHCONV=1

IMAGE_ARCHIVE="${IMAGE_ARCHIVE:-}"
CHECKSUM_FILE=""
IMAGE_NAME="${IMAGE_NAME:-coremasterkb-app:latest}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-cmkb}"
APP_CONTAINER_NAME="${APP_CONTAINER_NAME:-cmkb}"
TMP_CONTAINER_NAME="${TMP_CONTAINER_NAME:-tmp-deploy}"
if [ -n "${COMPOSE_CONFIG_CHECK_FILE:-}" ]; then
    CLEAN_COMPOSE_CONFIG_FILE=false
else
    COMPOSE_CONFIG_CHECK_FILE="/tmp/cmkb-compose-config.$$.yml"
    CLEAN_COMPOSE_CONFIG_FILE=true
fi
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-2}"
AUTH_CONFIG_PATH="main_control_service/config/system/auth.yaml"
# serving/mcp 内部密钥的宿主机持久化文件（docker compose 插值同源读取）。
SERVING_ENV_FILE="${SERVING_ENV_FILE:-.env}"
TMP_CONTAINER_ACTIVE=false
CODE_STAGE_DIR=""
CODE_BACKUP_DIR=""
CODE_SWAP_ACTIVE=false
CODE_INSTALLED_TARGETS=""
CODE_BACKED_UP_TARGETS=""

die() {
    echo "错误：$*" >&2
    exit 1
}

rollback_code_swap() {
    local target

    [ "$CODE_SWAP_ACTIVE" = true ] || return 0
    echo "=== 正在回滚未完成的宿主机代码切换 ===" >&2
    for target in $CODE_INSTALLED_TARGETS; do
        rm -rf -- "$target"
    done
    for target in $CODE_BACKED_UP_TARGETS; do
        if [ -e "$CODE_BACKUP_DIR/$target" ] || [ -L "$CODE_BACKUP_DIR/$target" ]; then
            mv "$CODE_BACKUP_DIR/$target" "$target"
        fi
    done
    CODE_SWAP_ACTIVE=false
}

cleanup_all() {
    rollback_code_swap
    if [ "$TMP_CONTAINER_ACTIVE" = true ]; then
        docker rm -f "$TMP_CONTAINER_NAME" >/dev/null 2>&1 || true
    fi
    if [ -n "$CODE_STAGE_DIR" ] && [ -d "$CODE_STAGE_DIR" ]; then
        rm -rf "$CODE_STAGE_DIR"
    fi
    if [ -n "$CODE_BACKUP_DIR" ] && [ -d "$CODE_BACKUP_DIR" ]; then
        rm -rf "$CODE_BACKUP_DIR"
    fi
    if [ "$CLEAN_COMPOSE_CONFIG_FILE" = true ]; then
        rm -f "$COMPOSE_CONFIG_CHECK_FILE"
    fi
}

trap cleanup_all EXIT

usage() {
    sed -n '2,7p' "$0"
}

compose() {
    if docker compose version >/dev/null 2>&1; then
        docker compose -p "$COMPOSE_PROJECT_NAME" "$@"
        return $?
    fi

    if command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" docker-compose "$@"
        return $?
    fi

    die "未找到 Docker Compose。请安装 Docker Compose v2（docker compose）或旧版 docker-compose。"
}

read_auth_config_value() {
    local key="$1"
    sed -n -E "s|^[[:space:]]*${key}:[[:space:]]*[\"']?([^\"']*)[\"']?[[:space:]]*(#.*)?$|\\1|p" \
        "$AUTH_CONFIG_PATH" | tail -n 1 | tr -d '\r'
}

generate_auth_secret() {
    local bytes="$1"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$bytes"
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "import secrets; print(secrets.token_hex(${bytes}))"
        return
    fi
    die "无法生成认证凭据：需要 openssl 或 python3。"
}

set_auth_secret() {
    local key="$1"
    local bytes="$2"
    local current secret_file tmp_file

    current="$(read_auth_config_value "$key")"
    if [ -n "$current" ] && [[ "$current" != change-me* ]]; then
        return
    fi
    if ! grep -Eq "^[[:space:]]*${key}:" "$AUTH_CONFIG_PATH"; then
        die "认证配置缺少 ${key} 字段：${AUTH_CONFIG_PATH}"
    fi

    secret_file="$(mktemp "${AUTH_CONFIG_PATH}.secret.XXXXXX")"
    tmp_file="$(mktemp "${AUTH_CONFIG_PATH}.tmp.XXXXXX")"
    chmod 600 -- "$secret_file" "$tmp_file"
    if ! generate_auth_secret "$bytes" > "$secret_file"; then
        rm -f -- "$secret_file" "$tmp_file"
        die "无法生成 ${key}。"
    fi
    if ! AUTH_SECRET_FILE="$secret_file" awk -v key="$key" '
        BEGIN {
            if ((getline value < ENVIRON["AUTH_SECRET_FILE"]) != 1 || value == "") exit 1
            close(ENVIRON["AUTH_SECRET_FILE"])
        }
        $0 ~ "^[[:space:]]*" key ":" {
            prefix = $0
            sub(/:.*/, ":", prefix)
            print prefix " \"" value "\""
            replaced = 1
            next
        }
        { print }
        END { exit(replaced ? 0 : 1) }
    ' "$AUTH_CONFIG_PATH" > "$tmp_file"; then
        rm -f -- "$secret_file" "$tmp_file"
        die "无法写入 ${key} 到 ${AUTH_CONFIG_PATH}。"
    fi
    mv -- "$tmp_file" "$AUTH_CONFIG_PATH"
    chmod 600 -- "$AUTH_CONFIG_PATH"
    rm -f -- "$secret_file"
    echo "已初始化 ${key}。"
}

set_bootstrap_initialization_complete() {
    local tmp_file
    tmp_file="$(mktemp "${AUTH_CONFIG_PATH}.tmp.XXXXXX")"
    chmod 600 -- "$tmp_file"
    if ! sed -E 's|^([[:space:]]*initialize_on_deploy:).*|\1 false|' \
        "$AUTH_CONFIG_PATH" > "$tmp_file"; then
        rm -f -- "$tmp_file"
        die "无法更新 bootstrap 初始化状态。"
    fi
    mv -- "$tmp_file" "$AUTH_CONFIG_PATH"
    chmod 600 -- "$AUTH_CONFIG_PATH"
}

read_serving_env_value() {
    local key="$1"
    sed -n -E "s|^${key}=(.+)\$|\1|p" "$SERVING_ENV_FILE" 2>/dev/null \
        | tail -n 1 | tr -d '\r'
}

ensure_serving_secrets() {
    # serving 与 mcp_server 的内部密钥（同容器 supervisor 子服务共享容器
    # 环境）：首次部署随机生成，持久化到宿主机 .env（600；已 gitignore），
    # 后续部署复用不轮换——与 auth.yaml 的 generate-once 语义一致。
    # 重建容器（start_and_verify 的 --force-recreate）即全服务重启生效。
    local key value

    [ -L "$SERVING_ENV_FILE" ] && \
        die "serving 密钥文件不能是符号链接：${SERVING_ENV_FILE}"
    [ -f "$SERVING_ENV_FILE" ] || : > "$SERVING_ENV_FILE"
    chmod 600 -- "$SERVING_ENV_FILE"

    for key in SERVING_EVIDENCE_REF_SECRET SERVING_INTERNAL_AUTH_SECRET; do
        # .env 是宿主机真相源（运维改文件即生效）；文件缺失才看进程环境，
        # 两者皆无才生成。生成后回写，跨部署复用不轮换。
        value="$(read_serving_env_value "$key")"
        [ -n "$value" ] || value="${!key:-}"
        if [ -z "$value" ]; then
            value="$(generate_auth_secret 32)"
            echo "已初始化 ${key}。"
        fi
        if ! grep -q "^${key}=" "$SERVING_ENV_FILE"; then
            printf '%s=%s\n' "$key" "$value" >> "$SERVING_ENV_FILE"
        fi
        # 导出供 docker-compose.yml 变量插值（deploy 脚本必须从仓库根目录运行）。
        export "$key=$value"
    done
}

ensure_auth_config_secrets() {
    local bootstrap_initialization

    [ -L "$AUTH_CONFIG_PATH" ] && die "认证配置不能是符号链接：${AUTH_CONFIG_PATH}"
    [ -f "$AUTH_CONFIG_PATH" ] || die "缺少认证配置：${AUTH_CONFIG_PATH}"
    for key in jwt_secret internal_verify_secret initialize_on_deploy admin_password; do
        grep -Eq "^[[:space:]]*${key}:" "$AUTH_CONFIG_PATH" || \
            die "认证配置缺少 ${key} 字段：${AUTH_CONFIG_PATH}"
    done
    bootstrap_initialization="$(read_auth_config_value "initialize_on_deploy")"
    case "$bootstrap_initialization" in
        true|false) ;;
        *) die "bootstrap.initialize_on_deploy 必须为 true 或 false。" ;;
    esac
    chmod 600 -- "$AUTH_CONFIG_PATH"
    set_auth_secret "jwt_secret" 32
    set_auth_secret "internal_verify_secret" 32

    case "$bootstrap_initialization" in
        true)
            set_auth_secret "admin_password" 20
            set_bootstrap_initialization_complete
            echo "已完成首次管理员密码初始化。"
            ;;
        false|"")
            ;;
        *)
            die "bootstrap.initialize_on_deploy 必须为 true 或 false。"
            ;;
    esac
}

published_ports() {
    awk '
        /published:/ {
            for (i = 1; i <= NF; i++) {
                if ($i == "published:") {
                    port = $(i + 1)
                    gsub(/[^0-9]/, "", port)
                    if (port != "") print port
                }
            }
        }
    ' "$COMPOSE_CONFIG_CHECK_FILE"
}

validate_compose_config() {
    echo "=== 正在校验 Compose 配置 ==="
    : > "$COMPOSE_CONFIG_CHECK_FILE"
    chmod 600 "$COMPOSE_CONFIG_CHECK_FILE"
    if ! compose config > "$COMPOSE_CONFIG_CHECK_FILE"; then
        die "docker-compose.yml 无效或无法解析。"
    fi

    if ! grep -q "cmkb_net" "$COMPOSE_CONFIG_CHECK_FILE"; then
        die "docker-compose.yml 未定义 cmkb_net。请将最新的 docker-compose.yml 与本脚本一起上传。"
    fi

    if ! grep -q "172.30.30.0/24" "$COMPOSE_CONFIG_CHECK_FILE"; then
        echo "警告：Compose 子网不是默认的 172.30.30.0/24。"
        echo "请确认 PostgreSQL 的 pg_hba.conf 已放行 CMKB_DOCKER_SUBNET 指定的子网。"
    fi

    if published_ports | grep -qx "80"; then
        die "docker-compose.yml 将 nginx 发布到了宿主机 80 端口。请使用 8080 端口，或明确设置 CMKB_UI_PORT。"
    fi
}

preflight_ports() {
    local port container
    local conflict=false

    echo "=== 正在检查宿主机端口占用 ==="
    while IFS= read -r port; do
        [ -n "$port" ] || continue
        while IFS= read -r container; do
            [ -n "$container" ] || continue
            if [ "$container" != "$APP_CONTAINER_NAME" ]; then
                echo "错误：宿主机端口 $port 已被容器 '$container' 占用。" >&2
                conflict=true
            fi
        done < <(docker ps --format '{{.Names}}\t{{.Ports}}' | awk -F '\t' -v host_port="$port" 'index($2, ":" host_port "->") { print $1 }')
    done < <(published_ports | sort -u)

    if [ "$conflict" = true ]; then
        die "请停止冲突容器，或修改对应的 CMKB_*_PORT 配置后重试。"
    fi
}

require_host_config() {
    [ -f main_control_service/config/system/database.yaml ] || \
        die "宿主机缺少文件：main_control_service/config/system/database.yaml"
    [ -f main_control_service/config/system/llm_service.yaml ] || \
        die "宿主机缺少文件：main_control_service/config/system/llm_service.yaml"
    [ -f main_control_service/config/domain_registry.yaml ] || \
        die "宿主机缺少文件：main_control_service/config/domain_registry.yaml"
    [ -d main_control_service/config/scenario_packs ] || \
        die "宿主机缺少目录：main_control_service/config/scenario_packs"
}

deployment_diagnostics() {
    echo "=== 失败时的服务状态 ===" >&2
    compose exec -T app supervisorctl status >&2 || true
    echo "=== 容器最近日志 ===" >&2
    compose logs --tail 100 app >&2 || true
}

wait_for_health() {
    local name="$1"
    local url="$2"
    local deadline=$((SECONDS + HEALTH_TIMEOUT))

    echo "正在等待 $name：$url"
    until compose exec -T app curl -fsS --max-time 3 "$url" >/dev/null 2>&1; do
        if [ "$SECONDS" -ge "$deadline" ]; then
            deployment_diagnostics
            die "$name 在 ${HEALTH_TIMEOUT} 秒内未达到健康状态：$url"
        fi
        sleep "$HEALTH_INTERVAL"
    done
    echo "正常：$name"
}

json_health_matches() {
    local url="$1"
    local expected_status="$2"
    local response

    if ! response="$(compose exec -T app curl -fsS --max-time 3 "$url" 2>/dev/null)"; then
        return 1
    fi
    printf '%s\n' "$response" | grep -Eq \
        "\"status\"[[:space:]]*:[[:space:]]*\"(${expected_status})\""
}

wait_for_json_health() {
    local name="$1"
    local url="$2"
    local expected_status="$3"
    local supervisor_name="$4"
    local deadline=$((SECONDS + HEALTH_TIMEOUT))
    local state

    echo "正在等待 $name：$url（期望状态=$expected_status）"
    until json_health_matches "$url" "$expected_status"; do
        state="$(compose exec -T app supervisorctl status "$supervisor_name" 2>/dev/null | awk '{print $2}' || true)"
        if [ "$state" = "FATAL" ] || [ "$state" = "EXITED" ]; then
            deployment_diagnostics
            die "$name 在达到健康状态前进入 Supervisor 状态 $state。"
        fi
        if [ "$SECONDS" -ge "$deadline" ]; then
            deployment_diagnostics
            die "$name 在 ${HEALTH_TIMEOUT} 秒内未达到健康状态：$url"
        fi
        sleep "$HEALTH_INTERVAL"
    done
    echo "正常：$name"
}

wait_for_http_response() {
    local name="$1"
    local url="$2"
    local deadline=$((SECONDS + HEALTH_TIMEOUT))

    echo "正在等待 $name 接受 HTTP 请求：$url"
    until compose exec -T app curl -sS --max-time 3 -o /dev/null "$url" >/dev/null 2>&1; do
        if [ "$SECONDS" -ge "$deadline" ]; then
            deployment_diagnostics
            die "$name 在 ${HEALTH_TIMEOUT} 秒内未能接受 HTTP 请求：$url"
        fi
        sleep "$HEALTH_INTERVAL"
    done
    echo "正常：$name 可以访问"
}

verify_mounted_file() {
    local host_path="$1"
    local container_path="$2"
    local host_hash container_output container_hash

    host_hash="$(sha256sum "$host_path" | awk '{print $1}')"
    if ! container_output="$(compose exec -T app sha256sum "$container_path" 2>/dev/null)"; then
        die "无法读取容器内的挂载文件：$container_path"
    fi
    container_hash="$(printf '%s\n' "$container_output" | awk '{print $1}' | tr -d '\r')"

    if [ -z "$container_hash" ] || [ "$host_hash" != "$container_hash" ]; then
        echo "宿主机校验值：$host_hash（$host_path）" >&2
        echo "容器内校验值：$container_hash（$container_path）" >&2
        die "宿主机与容器内配置不一致。请使用 --apply-config 重建容器。"
    fi
    echo "一致：$host_path -> $container_path"
}

check_control_plane_postgresql() {
    compose exec -T app python - <<'PY'
from pathlib import Path

import psycopg
import yaml

path = Path("/app/main_control_service/config/system/database.yaml")
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
cfg = data.get("default") or {}
required = ("host", "port", "dbname", "user", "password")
missing = [key for key in required if not cfg.get(key)]
if missing:
    raise SystemExit(f"database.yaml 的 default 缺少字段：{', '.join(missing)}")

kwargs = {
    "host": str(cfg["host"]),
    "port": int(cfg["port"]),
    "dbname": str(cfg["dbname"]),
    "user": str(cfg["user"]),
    "password": str(cfg["password"]),
    "sslmode": str(cfg.get("sslmode", "disable")),
    "gssencmode": str(cfg.get("gssencmode", "disable")),
    "connect_timeout": 5,
}
maintenance_kwargs = dict(kwargs)
maintenance_kwargs["dbname"] = "postgres"
with psycopg.connect(**maintenance_kwargs) as conn:
    exists = conn.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s", (kwargs["dbname"],)
    ).fetchone()
    if not exists:
        raise SystemExit(f"目标数据库不存在：{kwargs['dbname']}")
with psycopg.connect(**kwargs) as conn:
    conn.execute("SELECT 1").fetchone()
print(f"database.yaml：PostgreSQL 可以连接：{kwargs['host']}:{kwargs['port']}/{kwargs['dbname']}")
PY
}

check_mining_postgresql() {
    compose exec -T app python - <<'PY'
from pathlib import Path

import psycopg
import yaml
from psycopg.conninfo import conninfo_to_dict

from knowledge_mining.mining.infra.domain_db import resolve_domain_database
from knowledge_mining.mining.infra.pg_config import MiningDbConfig

mining_cfg = MiningDbConfig()
with psycopg.connect(mining_cfg.maintenance_conninfo, connect_timeout=5) as conn:
    exists = conn.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s", (mining_cfg.pg_dbname,)
    ).fetchone()
    if not exists:
        raise SystemExit(f"Mining 目标数据库不存在：{mining_cfg.pg_dbname}")

targets = [("Mining 的 PG_*", mining_cfg.conninfo)]
registry_path = Path("/app/main_control_service/config/domain_registry.yaml")
registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
seen_databases = {tuple(sorted(conninfo_to_dict(mining_cfg.conninfo).items()))}
for domain_id, entry in (registry.get("domains") or {}).items():
    if not isinstance(entry, dict) or not entry.get("enabled", True):
        continue
    resolved = resolve_domain_database(entry, mining_cfg)
    key = tuple(sorted(conninfo_to_dict(resolved.conninfo).items()))
    if key in seen_databases:
        continue
    seen_databases.add(key)
    targets.append((f"领域 {domain_id}（{resolved.source}）", resolved.conninfo))

for label, conninfo in targets:
    with psycopg.connect(conninfo, connect_timeout=5) as conn:
        conn.execute("SELECT 1").fetchone()
    print(f"{label}：PostgreSQL 连接正常")
PY
}

preflight_postgresql() {
    local output

    echo "=== 正在从容器内部验证 PostgreSQL 连接 ==="
    if ! output="$(check_control_plane_postgresql 2>&1)"; then
        echo "$output" >&2
        deployment_diagnostics
        die "容器使用 database.yaml 连接数据库失败。DBeaver 可以连接并不能证明 Docker 子网已放行，也不能证明容器使用的账号配置正确。"
    fi
    echo "$output"

    if ! output="$(check_mining_postgresql 2>&1)"; then
        echo "$output" >&2
        deployment_diagnostics
        die "Mining 的 PostgreSQL 连接验证失败。请检查 main_control_service/config/system/database.yaml 的 default 块，以及 domain_registry.yaml 中各领域的内联 database: 块。"
    fi
    echo "$output"
}

wait_for_supervisor() {
    local deadline=$((SECONDS + 30))

    echo "=== 正在等待 Supervisor 控制接口 ==="
    until compose exec -T app supervisorctl pid >/dev/null 2>&1; do
        if [ "$SECONDS" -ge "$deadline" ]; then
            compose logs --tail 100 app >&2 || true
            die "Supervisor 在 30 秒内未就绪。"
        fi
        sleep 1
    done
}

supervisor_start() {
    local service="$1"

    echo "正在启动 $service"
    if ! compose exec -T app supervisorctl start "$service"; then
        deployment_diagnostics
        die "Supervisor 无法启动 $service。"
    fi
}

start_services_in_dependency_order() {
    wait_for_supervisor

    # 主控服务向大模型服务提供统一配置。在启动依赖数据库的 Python 服务前，
    # 先从容器内部验证 PostgreSQL 连接。
    supervisor_start "control"
    wait_for_json_health "main_control_service" "http://127.0.0.1:8910/health" "ok" "control"
    verify_mounted_configs
    preflight_postgresql

    supervisor_start "llm_service"
    wait_for_json_health "llm_service" "http://127.0.0.1:8900/health" "ok" "llm_service"

    supervisor_start "mining"
    wait_for_json_health "knowledge_mining" "http://127.0.0.1:8901/health" "ok" "mining"

    supervisor_start "serving"
    wait_for_json_health "agent_serving_java" "http://127.0.0.1:8081/actuator/health" "ok|UP" "serving"

    supervisor_start "mcp"
    wait_for_http_response "mcp_server" "http://127.0.0.1:9000/mcp"

    supervisor_start "nginx"
    wait_for_health "nginx/kb-ui" "http://127.0.0.1/"
}

verify_mounted_configs() {
    echo "=== 正在校验宿主机与容器内的配置文件 ==="
    verify_mounted_file main_control_service/config/system/database.yaml /app/main_control_service/config/system/database.yaml
    verify_mounted_file main_control_service/config/system/llm_service.yaml /app/main_control_service/config/system/llm_service.yaml
    verify_mounted_file main_control_service/config/domain_registry.yaml /app/main_control_service/config/domain_registry.yaml
}

show_completion() {
    local host_ip ui_port
    host_ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    [ -n "$host_ip" ] || host_ip="<server-ip>"
    ui_port="$(published_ports | head -n 1 || true)"
    [ -n "$ui_port" ] || ui_port="8080"

    echo "=== 服务状态 ==="
    compose exec -T app supervisorctl status
    echo ""
    echo "=== 部署完成 ==="
    echo "前端地址：http://${host_ip}:${ui_port}"
    echo "修改宿主机配置后，请执行：bash deploy-server.sh --apply-config"
}

sync_serving_jar() {
    # jar 走 bind-mount（./runtime，容器 /app/runtime）：宿主机目录即真相源。
    # 若宿主机存在新构建的 jar（mvn -DskipTests package），落盘到 ./runtime；
    # 否则沿用 ./runtime 现有版本（首次部署时已从镜像补齐）。容器重建不回退。
    local jar
    jar="$(ls -t agent_serving_java/target/agent-serving-*.jar 2>/dev/null | head -1 || true)"
    if [ -n "$jar" ]; then
        echo "=== 正在同步 serving jar: $(basename "$jar") ==="
        mkdir -p runtime
        # 先写临时名再原子 mv：避免写一半时 serving 重启读到残缺 jar。
        cp -- "$jar" runtime/agent_serving.jar.tmp
        mv -- runtime/agent_serving.jar.tmp runtime/agent_serving.jar
    else
        echo "=== 未发现本地构建的 serving jar，沿用 ./runtime 现有版本 ==="
    fi
}

sync_kb_ui_dist() {
    # 前端同理：./kb-ui-dist bind-mount（容器 /app/kb-ui-dist）。
    # 宿主机 kb-ui/dist 存在（npm run build 产物）则原子替换宿主机目录。
    if [ -d kb-ui/dist ]; then
        echo "=== 正在同步 kb-ui 前端产物 ==="
        rm -rf kb-ui-dist.new
        cp -a kb-ui/dist kb-ui-dist.new
        rm -rf kb-ui-dist.old
        mv kb-ui-dist kb-ui-dist.old
        mv kb-ui-dist.new kb-ui-dist
        rm -rf kb-ui-dist.old
    else
        echo "=== 未发现 kb-ui/dist（先 cd kb-ui && npm run build），沿用现有前端 ==="
    fi
}

start_and_verify() {
    local previous_ordered_startup="${CMKB_ORDERED_STARTUP-}"
    local ordered_startup_was_set="${CMKB_ORDERED_STARTUP+x}"
    local compose_result=0

    echo "=== 正在重建容器 ==="
    export CMKB_ORDERED_STARTUP=1
    if compose up -d --force-recreate; then
        compose_result=0
    else
        compose_result=$?
    fi
    if [ -n "$ordered_startup_was_set" ]; then
        export CMKB_ORDERED_STARTUP="$previous_ordered_startup"
    else
        unset CMKB_ORDERED_STARTUP
    fi
    [ "$compose_result" -eq 0 ] || die "Docker Compose 无法重建应用容器。"

    sync_serving_jar
    sync_kb_ui_dist
    start_services_in_dependency_order
    show_completion
}

remove_existing_app() {
    echo "=== 正在删除现有应用容器 ==="
    compose stop app 2>/dev/null || true
    compose rm -f app 2>/dev/null || true

    if [ "$(docker ps -a --filter "name=^/${APP_CONTAINER_NAME}$" --format '{{.Names}}')" = "$APP_CONTAINER_NAME" ]; then
        docker rm -f "$APP_CONTAINER_NAME"
    fi
}

cleanup_tmp_container() {
    docker rm -f "$TMP_CONTAINER_NAME" >/dev/null 2>&1 || true
    TMP_CONTAINER_ACTIVE=false
}

replace_code_preserving_config() {
    local target

    echo "=== --force：使用镜像代码覆盖宿主机代码，并保留宿主机配置 ==="
    CODE_BACKUP_DIR="$(mktemp -d "${PWD}/.cmkb-code-backup.XXXXXX")"
    CODE_SWAP_ACTIVE=true
    CODE_INSTALLED_TARGETS=""
    CODE_BACKED_UP_TARGETS=""

    for target in knowledge_mining llm_service main_control_service mcp_server databases kb-ui-dist runtime reset_db.py releases.json; do
        if [ -e "$target" ] || [ -L "$target" ]; then
            mv "$target" "$CODE_BACKUP_DIR/$target"
            CODE_BACKED_UP_TARGETS="$CODE_BACKED_UP_TARGETS $target"
        fi
        if [ "$target" = "releases.json" ]; then
            # 版本号跟随镜像 --force 走（与 deploy-build.sh 打包版本一致）；
            # 宿主机已有版本号想保留的话，走增量同步链路管理。
            docker cp "$TMP_CONTAINER_NAME:/app/$target" "$CODE_STAGE_DIR/$target"
        fi
        mv "$CODE_STAGE_DIR/$target" "$target"
        CODE_INSTALLED_TARGETS="$CODE_INSTALLED_TARGETS $target"
    done

    CODE_SWAP_ACTIVE=false
    rm -rf "$CODE_BACKUP_DIR"
    CODE_BACKUP_DIR=""
    rm -rf "$CODE_STAGE_DIR"
    CODE_STAGE_DIR=""
}

stage_code_from_image() {
    local dir

    CODE_STAGE_DIR="$(mktemp -d "${PWD}/.cmkb-code-stage.XXXXXX")"
    echo "=== 正在从镜像暂存完整代码快照 ==="
    for dir in knowledge_mining llm_service main_control_service mcp_server databases kb-ui-dist runtime; do
        mkdir -p "$CODE_STAGE_DIR/$dir"
        docker cp "$TMP_CONTAINER_NAME:/app/$dir/." "$CODE_STAGE_DIR/$dir/"
    done
    docker cp "$TMP_CONTAINER_NAME:/app/reset_db.py" "$CODE_STAGE_DIR/reset_db.py"
    docker cp "$TMP_CONTAINER_NAME:/app/releases.json" "$CODE_STAGE_DIR/releases.json"

    if [ "$FORCE_CONFIG" = false ] && [ -d main_control_service/config ]; then
        rm -rf "$CODE_STAGE_DIR/main_control_service/config"
        cp -a main_control_service/config "$CODE_STAGE_DIR/main_control_service/config"
        echo "=== 已将宿主机完整配置目录复制到暂存快照 ==="
    fi
}

apply_config_only() {
    echo "=== 正在应用宿主机现有配置（不会替换镜像和文件） ==="
    require_host_config
    ensure_auth_config_secrets
    ensure_serving_secrets
    validate_compose_config
    preflight_ports
    start_and_verify
}

deploy_from_image() {
    # 默认选择当前目录中的版本化离线镜像；也允许显式指定 IMAGE_ARCHIVE。
    # 兼容 .tar.gz（当前格式）与 .tar.zst（历史格式）。
    if [ -z "$IMAGE_ARCHIVE" ]; then
        IMAGE_ARCHIVE="$(find . -maxdepth 1 -type f \( -name 'cmkb-*.tar.gz' -o -name 'cmkb-*.tar.zst' \) -print -quit)"
    fi

    # 当前目录有镜像归档就先校验再加载；没有则使用已存在的本地镜像。
    if [ -n "$IMAGE_ARCHIVE" ] && [ -f "$IMAGE_ARCHIVE" ]; then
        CHECKSUM_FILE="${IMAGE_ARCHIVE}.sha256"
        [ -f "$CHECKSUM_FILE" ] || die "缺少校验文件：$CHECKSUM_FILE"
        echo "=== 校验离线镜像完整性 ==="
        sha256sum -c "$CHECKSUM_FILE" || die "离线镜像校验失败，请重新传输"

        echo "=== 加载离线镜像：$IMAGE_ARCHIVE ==="
        case "$IMAGE_ARCHIVE" in
            *.tar.zst)
                command -v zstd >/dev/null || die "未安装 zstd，无法解压 $IMAGE_ARCHIVE（新格式为 .tar.gz，建议重新导出）"
                zstd -dc "$IMAGE_ARCHIVE" | docker load
                ;;
            *)
                gzip -dc "$IMAGE_ARCHIVE" | docker load
                ;;
        esac
    elif docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
        echo "=== 未找到离线镜像归档，使用已存在的本地镜像：$IMAGE_NAME ==="
    else
        die "既无 cmkb-*.tar.(gz|zst) 离线镜像，本地也没有镜像 $IMAGE_NAME。"
    fi

    echo "=== 验证镜像内的文档解析依赖 ==="
    docker run --rm "$IMAGE_NAME" sh -ec '
      command -v libreoffice >/dev/null || { echo "错误：镜像内未安装 LibreOffice" >&2; exit 1; }
      libreoffice --headless --version
      python -c "import openpyxl, xlrd; print(f\"依赖验证通过：openpyxl={openpyxl.__version__}，xlrd={xlrd.__version__}\")"
    ' || die "文档解析依赖验证失败，已停止部署"

    cleanup_tmp_container
    docker create --name "$TMP_CONTAINER_NAME" "$IMAGE_NAME" >/dev/null
    TMP_CONTAINER_ACTIVE=true

    validate_compose_config
    preflight_ports
    if [ "$FORCE" = true ]; then
        stage_code_from_image
    fi
    remove_existing_app

    if [ "$FORCE" = true ]; then
        replace_code_preserving_config
    else
        echo "=== 代码目录仅在缺失或为空时从镜像补齐 ==="
        for dir in knowledge_mining llm_service main_control_service mcp_server databases kb-ui-dist runtime; do
            if [ ! -d "$dir" ] || [ -z "$(ls -A "$dir" 2>/dev/null)" ]; then
                mkdir -p "$dir"
                docker cp "$TMP_CONTAINER_NAME:/app/$dir/." "./$dir/"
            fi
        done
        if [ ! -f reset_db.py ]; then
            docker cp "$TMP_CONTAINER_NAME:/app/reset_db.py" ./reset_db.py
        fi
        # 发布清单：宿主机真相源（compose 单文件挂载到 /app/releases.json）。
        # 已存在则保留——内网版本号不被外网镜像重置。
        if [ ! -f releases.json ]; then
            docker cp "$TMP_CONTAINER_NAME:/app/releases.json" ./releases.json
        fi

        if [ "$FORCE_CONFIG" = true ]; then
            echo "=== --force-config：正在使用镜像内容覆盖主控服务配置 ==="
            rm -rf main_control_service/config
            mkdir -p main_control_service/config
            docker cp "$TMP_CONTAINER_NAME:/app/main_control_service/config/." ./main_control_service/config/
        elif [ ! -d main_control_service/config ] || [ -z "$(ls -A main_control_service/config 2>/dev/null)" ]; then
            echo "=== 主控服务配置仅在缺失或为空时从镜像补齐 ==="
            mkdir -p main_control_service/config
            docker cp "$TMP_CONTAINER_NAME:/app/main_control_service/config/." ./main_control_service/config/
        fi
    fi

    cleanup_tmp_container
    require_host_config
    ensure_auth_config_secrets
    ensure_serving_secrets
    start_and_verify
}

FORCE=false
FORCE_CONFIG=false
APPLY_CONFIG=false

for arg in "$@"; do
    case "$arg" in
        --force) FORCE=true ;;
        --force-config) FORCE_CONFIG=true ;;
        --apply-config) APPLY_CONFIG=true ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) die "未知参数：$arg（使用 --help 查看用法）" ;;
    esac
done

case "$HEALTH_TIMEOUT" in
    ''|*[!0-9]*) die "HEALTH_TIMEOUT 必须是正整数，单位为秒。" ;;
esac
[ "$HEALTH_TIMEOUT" -gt 0 ] || die "HEALTH_TIMEOUT 必须大于 0。"

if [ "$APPLY_CONFIG" = true ] && { [ "$FORCE" = true ] || [ "$FORCE_CONFIG" = true ]; }; then
    die "--apply-config 不能与 --force 或 --force-config 同时使用。"
fi

if [ "$APPLY_CONFIG" = true ]; then
    apply_config_only
else
    deploy_from_image
fi
