# MinIO 内网离线部署手册

本文用于把 CoreMasterKB 使用的 MinIO 服务端镜像从外网环境带入内网，并部署为一个**不包含历史对象数据的全新 MinIO 实例**。

照本文部署后，内网将具备：

- 固定版本的 MinIO 服务端；
- 独立持久化数据目录；
- `source`、`staging`、`parse`、`binary` 四个业务桶；
- 独立的管理员凭据和最小权限业务凭据；
- 与 CoreMasterKB 应用一致的 Docker 网络和访问地址。

本文不迁移外网 MinIO 的 `/data`、历史文件、旧用户或旧密钥。

## 1. 先理解部署边界

MinIO 不是关系型数据库，也没有数据库表。

| 内容 | 实际位置 | 是否随 `docker save` 导出 |
|------|----------|:-------------------------:|
| MinIO 服务程序 | Docker 镜像 | 是 |
| Bucket、对象、对象版本 | MinIO `/data` 数据目录 | 否 |
| MinIO 内部 IAM/配置 | MinIO `/data` 下的内部元数据 | 否 |
| 文件目录、权限、文档记录、对象引用 | PostgreSQL | 否 |

本项目以 PostgreSQL 为业务事实源，MinIO 只保存对象字节和少量对象元数据。MinIO 中的 `/` 只是 object key 前缀，不是真实目录。

因此，“不要历史内容”的正确部署方式是：

1. 只导出并导入 MinIO 服务端镜像；
2. 在内网创建新的空数据目录；
3. 创建四个空 Bucket；
4. 创建全新的管理员和业务凭据；
5. 使用全新数据库，或者确保 PostgreSQL 中不存在指向旧 MinIO 对象的业务记录。

> **禁止组合：旧 PostgreSQL 业务数据 + 空 MinIO。** 旧数据库中的文档、Snapshot 和 Storage Object 记录会指向不存在的对象；重复上传时还可能错误复用已经不存在的对象引用。

## 2. 本项目当前的 MinIO 约定

当前固定使用以下四个 Bucket：

| Bucket | 用途 | 初次部署是否需要对象 |
|--------|------|:--------------------:|
| `agentickb-dev-source` | 用户上传的原始文件 | 否 |
| `agentickb-dev-staging` | 尚未提交的临时上传对象 | 否 |
| `agentickb-dev-parse` | 解析 IR、解析原始结果等 | 否 |
| `agentickb-dev-binary` | 图片、页面渲染等二进制产物 | 否 |

四个 Bucket 必须存在，但可以完全为空。后续上传和解析会自然生成 object key，不需要预建目录或占位文件。

应用正常启动只构造 MinIO 客户端，不会自动创建 Bucket。仓库中的 `ensure_buckets()` 主要用于测试和人工验收，不能当作生产环境的自动迁移机制。

## 3. 离线制品清单

联网服务器准备一个不含数据、不含密码的目录：

```text
offline-clean/
├── agentickb-minio-RELEASE.2025-09-07T16-13-09Z-amd64.tar
├── agentickb-minio-RELEASE.2025-09-07T16-13-09Z-amd64.tar.sha256
├── mc.RELEASE.2025-08-13T08-35-41Z
├── mc.RELEASE.2025-08-13T08-35-41Z.sha256sum
├── docker-compose.yml
└── agentickb-app-policy.json
```

当前镜像信息：

```text
镜像：agentickb-minio:RELEASE.2025-09-07T16-13-09Z
系统：Linux
架构：amd64
```

### 固定版本安全提醒

`RELEASE.2025-09-07T16-13-09Z` 是对外网现状的兼容复刻版本。MinIO 官方后续披露了一个影响此前版本的高危 Service Account/STS 权限提升问题，并在 `RELEASE.2025-10-15T17-29-55Z` 修复。

本手册为了先完成同版本离线迁移，仍记录当前镜像，但正式生产上线必须登记该风险并安排升级验证。在升级完成前：

- 只创建普通静态业务用户；
- 不使用 Service Account 或 STS；
- MinIO API 仅开放给必要的内网网段；
- 不把“未使用受影响功能”当作官方修复的替代品。

`mc` 是 MinIO 官方管理客户端。固定的 2025-09 服务端社区版不应依赖 Web Console 创建 IAM 用户和策略，因此 `mc` 是全新部署的必需制品，不是可选工具。

在联网机器下载与服务端发布时间接近的固定版本 Linux amd64 客户端：

```bash
curl --fail --location --remote-name \
  https://dl.min.io/client/mc/release/linux-amd64/archive/mc.RELEASE.2025-08-13T08-35-41Z

curl --fail --location --remote-name \
  https://dl.min.io/client/mc/release/linux-amd64/archive/mc.RELEASE.2025-08-13T08-35-41Z.sha256sum
```

将这两个文件加入 `offline-clean` 后再带入内网。不要使用不固定版本的 `latest`。

以下文件不得带入内网：

- 外网 MinIO 的 `.env`；
- 外网 `app-credentials.env`；
- `agentickb-minio-data-*.tar.gz`；
- `agentickb-minio-secrets.tar.enc`；
- 会恢复旧数据或旧密钥的 `restore.sh`。

## 4. 内网服务器前置检查

将 `offline-clean` 上传到内网服务器，例如：

```text
/opt/agentickb-minio
```

进入目录：

```bash
cd /opt/agentickb-minio
```

检查 CPU 架构、Docker 和 Compose：

```bash
uname -m
docker --version
docker compose version
```

期望 `uname -m` 返回 `x86_64`。当前离线镜像是 `amd64`，不能直接运行在 ARM64 服务器上。

检查端口是否占用：

```bash
ss -lntp | grep -E ':(19000|19001)\b' || true
```

期望没有输出。默认端口用途为：

- `19000`：MinIO S3 API；
- `19001`：MinIO 管理控制台，仅绑定宿主机回环地址。

## 5. 加载离线镜像

离线传输后必须执行 SHA-256 校验：

```bash
sha256sum -c agentickb-minio-RELEASE.2025-09-07T16-13-09Z-amd64.tar.sha256
sha256sum -c mc.RELEASE.2025-08-13T08-35-41Z.sha256sum
```

加载镜像：

```bash
docker load -i agentickb-minio-RELEASE.2025-09-07T16-13-09Z-amd64.tar
```

确认镜像存在：

```bash
docker image inspect agentickb-minio:RELEASE.2025-09-07T16-13-09Z \
  --format 'ID={{.Id}} ARCH={{.Architecture}} OS={{.Os}} USER={{json .Config.User}}'
```

期望架构为 `amd64`、系统为 `linux`。

授权并确认 `mc` 版本：

```bash
chmod 700 mc.RELEASE.2025-08-13T08-35-41Z
./mc.RELEASE.2025-08-13T08-35-41Z --version
```

## 6. 准备 Docker 网络

MinIO 的独立 Compose 文件把 `cmkb_net` 声明为 external network，因此该网络必须先存在。这个网络应由 CoreMasterKB 主 Compose 创建和管理，不要在全新服务器上先随意创建一个同名网络，否则后续主 Compose 可能因网络所有权或配置不一致而失败。

检查：

```bash
docker network inspect cmkb_net >/dev/null 2>&1 && echo "cmkb_net 已存在"
```

如果网络已存在，不做任何修改。

如果是全新服务器，先准备好 CoreMasterKB 应用目录和已经加载的应用镜像，然后在 CoreMasterKB 项目根目录只创建容器和网络、不启动应用：

```bash
docker compose -p cmkb create --no-build app
```

再次确认：

```bash
docker network inspect cmkb_net --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}}{{end}}'
```

默认应看到 `cmkb_net` 和 `172.30.30.0/24`。此时应用容器只是 created 状态，不会抢先启动；完成 MinIO 初始化后再按正常流程部署或启动应用。

如果暂时还没有 CoreMasterKB 应用离线包，先停止在这里，等主应用 Compose 可以创建网络后再启动 MinIO。不要删除或重建正在被其他容器使用的 `cmkb_net`。

## 7. 创建全新数据目录

本部署使用宿主机目录：

```text
/srv/agentickb/minio/data
```

创建目录：

```bash
mkdir -p /srv/agentickb/minio/data
chmod 750 /srv/agentickb/minio/data
```

首次启动如果日志提示 `/data` 无写权限，先查看镜像运行用户：

```bash
docker image inspect agentickb-minio:RELEASE.2025-09-07T16-13-09Z \
  --format '{{json .Config.User}}'
```

只有确认镜像使用的 UID/GID 后，才对数据目录执行对应的 `chown`。不要为了省事使用 `chmod 777`。

## 8. 创建新的管理员凭据

MinIO Compose 从当前目录的 `.env` 读取管理员凭据。此文件只在内网服务器生成，不从外网复制，也不提交到 Git。

先设置严格权限掩码：

```bash
umask 077
```

如果服务器有 OpenSSL，可生成随机管理员密码：

```bash
openssl rand -hex 32
```

创建 `/opt/agentickb-minio/.env`：

```dotenv
MINIO_ROOT_USER=<新的管理员用户名>
MINIO_ROOT_PASSWORD=<新的高强度随机密码>
```

设置权限：

```bash
chmod 600 /opt/agentickb-minio/.env
```

要求：

- 不得复用外网 MinIO 密码；
- 不得使用项目仓库中出现过的 access key 或 secret key；
- root 凭据仅用于首次初始化和灾备，不提供给业务应用；
- 凭据不得粘贴到工单、聊天、日志或 Git。

## 9. 准备 MinIO Compose

离线包中的 `docker-compose.yml` 应与下面结构一致：

```yaml
services:
  minio:
    image: agentickb-minio:RELEASE.2025-09-07T16-13-09Z
    container_name: agentickb-minio
    restart: unless-stopped

    command:
      - server
      - /data
      - --console-address
      - :9001

    env_file:
      - .env

    volumes:
      - /srv/agentickb/minio/data:/data

    ports:
      - "19000:9000"
      - "127.0.0.1:19001:9001"

    networks:
      cmkb_net:
        aliases:
          - minio

networks:
  cmkb_net:
    external: true
    name: cmkb_net
```

验证配置语法。该命令不应将 `.env` 内容复制到聊天或日志：

```bash
docker compose -f docker-compose.yml config -q
```

## 10. 启动 MinIO

启动：

```bash
docker compose -f docker-compose.yml up -d
```

检查容器：

```bash
docker ps --filter name=agentickb-minio \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

查看启动日志：

```bash
docker logs --tail 100 agentickb-minio
```

检查 API 健康状态：

```bash
curl -fsS http://127.0.0.1:19000/minio/health/live && echo
curl -fsS http://127.0.0.1:19000/minio/health/ready && echo
```

如果服务器没有 `curl`，可以先通过容器状态和日志判断，后续再从能访问内网 API 的机器验证。

## 11. 使用 `mc` 初始化 Bucket、策略和业务账号

固定的 2025-09 MinIO 社区版 Web Console 已精简为对象浏览器，不能把 IAM 用户和策略初始化依赖在 UI 上。本节使用离线带入的固定版本 `mc` 完成全部初始化。

### 11.1 准备最小权限策略文件

外网带入的 `agentickb-app-policy.json` 包含 `s3:CreateBucket`，不能原样用于长期业务账号。先在内网把文件替换为下面内容：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation",
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads"
      ],
      "Resource": [
        "arn:aws:s3:::agentickb-dev-source",
        "arn:aws:s3:::agentickb-dev-parse",
        "arn:aws:s3:::agentickb-dev-binary",
        "arn:aws:s3:::agentickb-dev-staging"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts"
      ],
      "Resource": [
        "arn:aws:s3:::agentickb-dev-source/*",
        "arn:aws:s3:::agentickb-dev-parse/*",
        "arn:aws:s3:::agentickb-dev-binary/*",
        "arn:aws:s3:::agentickb-dev-staging/*"
      ]
    }
  ]
}
```

### 11.2 建立临时管理会话

`mc alias set` 会把 root 凭据写进客户端配置。使用一个专门的临时配置目录，初始化完成后清理，不要污染 root 的默认 `~/.mc`。

在独占的运维终端执行，临时关闭 shell history：

```bash
set +o history

MC_BIN=/opt/agentickb-minio/mc.RELEASE.2025-08-13T08-35-41Z
MC_BOOTSTRAP_DIR=/opt/agentickb-minio/.mc-bootstrap

install -d -m 700 "$MC_BOOTSTRAP_DIR"

read -rp 'MinIO root 用户名：' MINIO_ROOT_USER
read -rsp 'MinIO root 密码：' MINIO_ROOT_PASSWORD
echo

"$MC_BIN" --config-dir "$MC_BOOTSTRAP_DIR" alias set \
  intranet http://127.0.0.1:19000 \
  "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
```

不要把终端输出、变量值或命令历史粘贴到聊天和工单。

### 11.3 创建四个空 Bucket 并启用 Versioning

```bash
for bucket in source staging parse binary; do
  "$MC_BIN" --config-dir "$MC_BOOTSTRAP_DIR" mb --ignore-existing \
    "intranet/agentickb-dev-${bucket}"

  "$MC_BIN" --config-dir "$MC_BOOTSTRAP_DIR" version enable \
    "intranet/agentickb-dev-${bucket}"
done
```

逐个验证：

```bash
for bucket in source staging parse binary; do
  "$MC_BIN" --config-dir "$MC_BOOTSTRAP_DIR" version info \
    "intranet/agentickb-dev-${bucket}"
done
```

四个 Bucket 初始可以完全为空。不要开启 Object Lock，不要上传占位文件，也不要创建所谓“目录”。

### 11.4 创建业务用户并绑定策略

先在密码管理系统中生成全新的业务 Access Key 和 Secret Key，然后在当前独占终端受控输入：

```bash
read -rp '新的业务 Access Key：' APP_ACCESS_KEY
read -rsp '新的业务 Secret Key：' APP_SECRET_KEY
echo
```

创建策略、用户并绑定：

```bash
"$MC_BIN" --config-dir "$MC_BOOTSTRAP_DIR" admin policy create \
  intranet agentickb-app-policy \
  /opt/agentickb-minio/agentickb-app-policy.json

"$MC_BIN" --config-dir "$MC_BOOTSTRAP_DIR" admin user add \
  intranet "$APP_ACCESS_KEY" "$APP_SECRET_KEY"

"$MC_BIN" --config-dir "$MC_BOOTSTRAP_DIR" admin policy attach \
  intranet agentickb-app-policy --user "$APP_ACCESS_KEY"

"$MC_BIN" --config-dir "$MC_BOOTSTRAP_DIR" admin policy info \
  intranet agentickb-app-policy

"$MC_BIN" --config-dir "$MC_BOOTSTRAP_DIR" admin user info \
  intranet "$APP_ACCESS_KEY"
```

要求：

- 不授予 `s3:CreateBucket`；
- 不授予管理员、用户管理、策略管理或服务更新权限；
- 使用普通 MinIO 用户，不使用 Service Account 或 STS；
- 业务凭据主副本保存在内网密码管理系统中；
- 不复用外网 `app-credentials.env` 中的旧凭据。

### 11.5 清理临时 root 会话

先移除 alias，再删除固定路径下的临时客户端配置：

```bash
"$MC_BIN" --config-dir "$MC_BOOTSTRAP_DIR" alias remove intranet

unset MINIO_ROOT_USER MINIO_ROOT_PASSWORD APP_ACCESS_KEY APP_SECRET_KEY
unset MC_BIN MC_BOOTSTRAP_DIR

rm -rf -- /opt/agentickb-minio/.mc-bootstrap
set -o history
```

`rm -rf` 的目标必须保持为上面这个确切目录，不得替换为环境变量、通配符或更宽路径。

### 11.6 Console 仅用于查看

如果需要查看 Bucket，可建立 SSH 隧道：

```bash
ssh -N -L 19001:127.0.0.1:19001 root@<内网服务器IP>
```

浏览器访问 `http://127.0.0.1:19001`。不要临时把 `19001` 改成 `0.0.0.0` 对全网开放，也不要依赖 Console 创建 IAM 用户或策略。

## 12. 配置 CoreMasterKB 访问内网 MinIO

修改内网服务器上的：

```text
main_control_service/config/system/storage.yaml
```

示例：

```yaml
object_store:
  provider: minio
  bucket_prefix: agentickb-dev-

  endpoint: minio.example.internal:19000
  access_key: <第11.4节创建的新业务Access Key>
  secret_key: <第11.4节创建的新业务Secret Key>
  secure: false
  region: null
```

### Endpoint 选择要求

项目会生成预签名 URL，让用户浏览器直接读取 MinIO 中的 PDF、图片等对象。因此 endpoint 必须同时满足：

1. CoreMasterKB 应用容器可以访问；
2. 内网用户浏览器可以访问和解析；
3. 防火墙只允许必要的内网网段访问；
4. endpoint 的主机名和端口与用户实际访问路径一致。

推荐使用内网 DNS，例如：

```text
minio.example.internal:19000
```

不建议在应用配置中写：

```text
minio:9000
```

`minio:9000` 只在 Docker 网络内部可解析，通常会导致浏览器拿到预签名 URL 后无法打开文件。

本手册给出的 Compose 在 `19000` 上提供的是明文 HTTP，所以对应 `secure: false`。

只有另行完成 TLS 证书、反向代理或 MinIO TLS 终止，并实际验证预签名下载、Range 和上传后，才能改成：

```yaml
endpoint: minio.example.internal:443
secure: true
```

不要只改 `secure: true` 就认为 HTTPS 已经部署完成，也不要通过关闭证书验证来绕过错误证书。

### 当前项目的凭据限制与 P0 上线门禁

当前实现仍从 `storage.yaml` 读取 `access_key` 和 `secret_key`，所以密码管理系统只能保存凭据主副本，应用运行前仍需受控写入该 YAML。

更严重的是，当前版本对 `GET /api/v1/system/*` 免登录放行，`storage` 普通接口和 `/raw` 都可能经 Nginx 的 `/api/control-plane/` 路径暴露给能够访问 UI 的用户。`chmod 600` 只能限制宿主机文件读取，不能阻止 HTTP 泄密。

正式上线前必须完成以下 P0 处置之一：

1. 长期方案：为内部配置接口增加服务间鉴权，并让外部响应永不包含 secret；
2. 最低临时门禁：在 Nginx 对外入口阻断以下两个精确路径，同时保留容器内服务通过 `127.0.0.1:8910` 拉取配置：

```nginx
location = /api/control-plane/api/v1/system/storage {
    return 404;
}

location = /api/control-plane/api/v1/system/storage/raw {
    return 404;
}
```

临时门禁需要更新 `docker/nginx.conf` 并重新构建、导入 CoreMasterKB 应用镜像；仅修改 MinIO Compose 不会生效。在完成门禁前，不得把 UI 暴露给非受信用户或宽泛内网网段。

此外：

- 该文件只能存在于内网部署主机；
- 设置最小文件权限；
- 不得从内网同步回 Git；
- 不得出现在部署日志和截图中。

应用仓库中曾出现过外网 MinIO 长期凭据。上线内网前必须吊销旧凭据；删除当前文件里的旧值不能替代凭据轮换。

修改完成后限制配置文件权限：

```bash
chmod 600 main_control_service/config/system/storage.yaml
```

## 13. 应用配置生效

先确认 MinIO 已启动、四个 Bucket 已创建、业务策略和账号已生效，再启动或重启 CoreMasterKB。

如果 CoreMasterKB 尚未部署，按项目现有全量离线部署流程部署。mining 启动时不会连接或探测 MinIO，因此服务启动顺序本身不是保证；必须在第一次上传、预览或解析前完成 MinIO、Bucket、Versioning、用户和策略初始化。

如果 CoreMasterKB 已经运行，在项目根目录应用配置：

```bash
bash deploy-server.sh --apply-config
```

该操作会保留宿主机配置，重建应用容器，并按依赖顺序执行检查和启动。

不要把外网的 `storage.yaml` 覆盖到内网，也不要用代码同步包覆盖内网 `main_control_service/config/`。

## 14. 上线验收

### 14.1 MinIO 服务验收

```bash
docker ps --filter name=agentickb-minio
docker logs --tail 100 agentickb-minio
curl -fsS http://127.0.0.1:19000/minio/health/live && echo
curl -fsS http://127.0.0.1:19000/minio/health/ready && echo
```

通过 `mc` 初始化输出和 Console 对象浏览页确认：

- 四个 Bucket 都存在；
- 四个 Bucket 都启用了 Versioning；
- 初始化命令已成功创建并绑定业务策略；
- 业务策略不包含 `s3:CreateBucket` 和管理员权限。

### 14.2 应用验收

在 CoreMasterKB 页面执行一个最小闭环：

1. 创建一个测试知识库；
2. 上传一个小型 `.txt` 或 `.md` 文件；
3. 确认上传成功；
4. 打开或下载该文件；
5. 触发一次解析；
6. 确认原文和解析结果可以读取；
7. 删除测试文档。

然后在 MinIO Console 检查：

- `agentickb-dev-source` 出现原始对象；
- `agentickb-dev-parse` 在解析后出现解析对象；
- `agentickb-dev-staging` 没有长期残留异常对象；
- `agentickb-dev-binary` 为空也可能是正常状态，只有生成图片等资产时才会写入。

### 14.3 浏览器直连验收

在用户实际使用的内网电脑上打开文档预览，并在浏览器开发者工具中确认预签名 URL：

- 使用的是内网 DNS/IP；
- 没有出现 `minio:9000`；
- 没有跳到外网地址；
- URL 在有效期内能返回对象；
- 大文件 Range 请求可以正常工作。

## 15. 常见故障

### `network cmkb_net declared as external, but could not be found`

原因：MinIO Compose 要求使用已有 external network。

处理：进入 CoreMasterKB 项目根目录，让主 Compose 创建应用容器和网络，但暂不启动应用：

```bash
docker compose -p cmkb create --no-build app
docker network inspect cmkb_net
```

不要先手工创建同名网络再让当前主 Compose 接管。

### `permission denied` 或 MinIO 无法写入 `/data`

原因：宿主机数据目录属主与镜像运行 UID/GID 不匹配。

处理：先检查镜像用户和日志，再把 `/srv/agentickb/minio/data` 的属主调整为确切 UID/GID。不要使用 `chmod 777`。

### `NoSuchBucket`

原因：四个 Bucket 没建齐，或 `bucket_prefix` 与实际桶名不一致。

检查：

```text
bucket_prefix: agentickb-dev-
```

必须对应：

```text
agentickb-dev-source
agentickb-dev-staging
agentickb-dev-parse
agentickb-dev-binary
```

### `AccessDenied`

原因通常包括：

- 应用使用了错误的新凭据；
- 业务用户没有绑定策略；
- 策略 Bucket 名与 `bucket_prefix` 不一致；
- 缺少对象版本或 Multipart 权限；
- 应用仍在使用旧配置。

不要通过把业务账号升级为 root 来掩盖策略问题。

### 应用上传正常，但浏览器无法预览

最常见原因是 endpoint 配置为 `minio:9000`、`127.0.0.1` 或其他只有服务器能访问的地址。

改成用户浏览器和应用容器都能访问的内网 DNS/IP，然后重新应用配置。

可以先从应用容器验证 DNS：

```bash
docker exec cmkb getent hosts minio.example.internal
```

然后必须再从用户实际使用的内网电脑验证相同域名和端口。容器能访问不代表浏览器一定能访问。

### `SignatureDoesNotMatch`

常见原因包括 endpoint 主机名或端口与实际请求不一致、HTTP/HTTPS 配置不一致、业务密钥填错，或者服务器时间偏差过大。

依次检查：

- `storage.yaml` 的 endpoint 不带 `http://` 或 `https://`；
- HTTP 对应 `secure: false`，HTTPS 对应 `secure: true`；
- 浏览器实际请求的主机名和端口与签名 URL 一致；
- 内网服务器已正确进行时间同步；
- 应用使用的是新建业务凭据，不是 root 或外网旧凭据。

### 应用启动成功，但第一次上传才报错

这是当前项目的预期故障表现之一：应用启动不会主动创建或完整校验 Bucket。必须回到第 11、12 节检查 Bucket、Versioning、用户和策略。

### 空 MinIO 上出现旧文档读取失败或重复上传异常

原因：内网 PostgreSQL 中保留了外网对象引用，但 MinIO 没迁旧对象。

处理原则：

- 如果不要旧业务数据，使用全新/正确清理后的 PostgreSQL；
- 如果必须保留旧业务数据，就必须迁移这些记录实际引用的 MinIO 对象，不能只迁 Bucket 名称。

## 16. 运维命令

启动：

```bash
cd /opt/agentickb-minio
docker compose up -d
```

停止但保留数据：

```bash
docker compose stop
```

重启：

```bash
docker compose restart
```

查看日志：

```bash
docker logs -f --tail 200 agentickb-minio
```

查看容器挂载：

```bash
docker inspect agentickb-minio \
  --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
```

删除容器但保留宿主机数据：

```bash
docker compose down
```

> `docker compose down` 不会删除 `/srv/agentickb/minio/data`。禁止手工删除该目录，除非已经明确批准清空全部对象数据。

## 17. 最终检查清单

- [ ] 内网服务器是 Linux amd64；
- [ ] MinIO 固定版本镜像已经加载；
- [ ] 没有恢复外网 `/data`；
- [ ] 没有复制外网 `.env` 或 `app-credentials.env`；
- [ ] `/srv/agentickb/minio/data` 是新的持久化目录；
- [ ] `cmkb_net` 已存在；
- [ ] MinIO API 健康；
- [ ] 管理 Console 只允许运维访问；
- [ ] 四个 Bucket 初始为空，并且已经创建且可用；
- [ ] 四个 Bucket 已启用 Versioning；
- [ ] 业务账号使用全新凭据；
- [ ] 业务策略不包含管理员权限和 `s3:CreateBucket`；
- [ ] 应用 endpoint 是浏览器和容器都能访问的内网地址；
- [ ] `storage.yaml` 没有沿用外网旧凭据；
- [ ] 外部访问 storage 配置接口的 P0 泄密路径已阻断或完成内部鉴权/脱敏；
- [ ] 旧 MinIO 凭据已吊销；
- [ ] PostgreSQL 与空 MinIO 的数据状态一致；
- [ ] 上传、读取、解析、预览和删除闭环验收通过。

## 18. 参考资料

- [MinIO 容器部署与持久化存储](https://github.com/minio/docs/blob/main/source/operations/deployments/baremetal-deploy-minio-as-a-container.rst)
- [MinIO `mc mb` 建桶命令](https://github.com/minio/docs/blob/main/source/reference/minio-mc/mc-mb.rst)
- [MinIO Bucket Versioning](https://github.com/minio/docs/blob/main/source/administration/object-management/object-versioning.rst)
- [MinIO 身份与权限管理](https://min.io/docs/minio/linux/administration/identity-access-management.html)
- [MinIO `mc` 固定版本发布记录](https://github.com/minio/mc/releases/tag/RELEASE.2025-08-13T08-35-41Z)
- [MinIO 2025-10-15 权限提升安全公告](https://github.com/minio/minio/security/advisories/GHSA-jjjj-jwhf-8rgr)
