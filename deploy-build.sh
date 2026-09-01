#!/bin/bash
# 联网构建机：构建、验证并导出可供离线部署的镜像
# 用法：bash deploy-build.sh
#
# 压缩格式 gzip（非 zstd）：内网服务器通常只有最基础工具，gzip/tar 必有；
# 几百 MB 级镜像 zstd 与 gzip 压缩率差异 1-3%，不值得为此要求内网装 zstd。

set -Eeuo pipefail

command -v docker >/dev/null || { echo "错误：未安装 Docker" >&2; exit 1; }

RELEASE_MANIFEST="releases.json"
[ -f "$RELEASE_MANIFEST" ] || { echo "错误：缺少发布清单 $RELEASE_MANIFEST" >&2; exit 1; }
MANIFEST_VERSION="$(sed -nE 's/^[[:space:]]*"current"[[:space:]]*:[[:space:]]*"([0-9]+\.[0-9]+\.[0-9]+)".*/\1/p' "$RELEASE_MANIFEST" | head -n 1)"
[ -n "$MANIFEST_VERSION" ] || { echo "错误：发布清单 current 必须是 MAJOR.MINOR.PATCH" >&2; exit 1; }
if [ -n "${CMKB_VERSION:-}" ] && [ "$CMKB_VERSION" != "$MANIFEST_VERSION" ]; then
  echo "错误：CMKB_VERSION=$CMKB_VERSION 与发布清单版本 $MANIFEST_VERSION 不一致" >&2
  exit 1
fi
VERSION="$MANIFEST_VERSION"
IMAGE_NAME="coremasterkb-app:${VERSION}"
ARCHIVE="cmkb-${VERSION}.tar.gz"

echo "=== 构建镜像：${IMAGE_NAME} ==="
docker compose build app
docker tag coremasterkb-app:latest "${IMAGE_NAME}"

echo "=== 验证镜像内的文档解析依赖 ==="
docker run --rm "${IMAGE_NAME}" sh -ec '
  command -v libreoffice >/dev/null || { echo "错误：未找到 LibreOffice" >&2; exit 1; }
  libreoffice --headless --version
  python -c "import openpyxl, xlrd; print(f\"依赖验证通过：openpyxl={openpyxl.__version__}，xlrd={xlrd.__version__}\")"
'

echo "=== 导出并压缩离线镜像：${ARCHIVE} ==="
docker save coremasterkb-app:latest "${IMAGE_NAME}" | gzip -6 > "${ARCHIVE}"
sha256sum "${ARCHIVE}" > "${ARCHIVE}.sha256"

echo "=== 离线镜像生成完成 ==="
ls -lh "${ARCHIVE}" "${ARCHIVE}.sha256"
echo "镜像版本：${VERSION}"
echo "请将 ${ARCHIVE} 和 ${ARCHIVE}.sha256 一起传到离线服务器。"
