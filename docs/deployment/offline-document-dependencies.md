# Word 与 Excel 离线部署依赖

知识挖掘支持 `.doc`、`.docx`、`.xls` 和 `.xlsx`。`.doc` 转换使用容器内的 LibreOffice Writer；Excel 读取使用容器内的 Python 包 `openpyxl` 和 `xlrd`。运行时不会下载依赖，也不会调用云端文档转换服务。

## 团队协作与制品分发

GitHub 只保存 Dockerfile、构建脚本、部署脚本和校验规则，不保存 LibreOffice 安装包或完整 Docker 镜像。

联网构建机生成以下两个文件，并将它们成对放入公司共享盘或内网制品库：

- `cmkb-2026.08.10.tar.zst`
- `cmkb-2026.08.10.tar.zst.sha256`

离线服务器部署前必须通过 SHA-256 校验。生产 `.env` 不进入 GitHub，也不写入 Docker 镜像，由部署主机挂载。

## 联网构建机

构建机需要 Docker、Docker Compose、Git 和 `zstd`，不需要在宿主机安装 LibreOffice、`openpyxl` 或 `xlrd`。这些依赖由 Dockerfile 安装并在镜像内验证。

```bash
CMKB_VERSION=2026.08.10 bash deploy-build.sh
```

构建脚本会完成以下工作：

1. 构建一体化 Docker 镜像。
2. 在容器内验证 LibreOffice、`openpyxl` 和 `xlrd`。
3. 导出并压缩版本化镜像。
4. 生成对应的 SHA-256 校验文件。

## 离线服务器

离线服务器需要 Docker、Docker Compose、`zstd`，以及成对传入的镜像归档和校验文件。

```bash
IMAGE_ARCHIVE=./cmkb-2026.08.10.tar.zst \
IMAGE_NAME=coremasterkb-app:2026.08.10 \
bash deploy-server.sh
```

部署脚本会先验证 SHA-256，再加载镜像，并在替换现有容器前验证镜像内的文档解析依赖。任一步失败都会停止部署。

如果归档同时包含 `coremasterkb-app:latest` 标签，也可以省略 `IMAGE_NAME`：

```bash
IMAGE_ARCHIVE=./cmkb-2026.08.10.tar.zst bash deploy-server.sh
```

## 非 Docker Python 服务

只有不使用项目 Docker 镜像时，才需要准备 Python wheelhouse：

```bash
python -m pip download --only-binary=:all: \
  --dest wheelhouse \
  "openpyxl>=3.1,<4" "xlrd>=2.0,<3"
```

将整个 `wheelhouse` 复制到离线主机后安装：

```bash
python -m pip install --no-index --find-links wheelhouse \
  "openpyxl>=3.1,<4" "xlrd>=2.0,<3"
```

`xlwt` 只用于测试生成旧版 `.xls` 样本，不应安装到生产运行环境。

## 上线检查

Docker 构建和部署脚本都会执行等效检查。如需人工复查，可在运行中的容器执行：

```bash
docker compose exec -T app sh -ec '
  libreoffice --headless --version
  python -c "import openpyxl, xlrd; print(openpyxl.__version__, xlrd.__version__)"
'
```

如果 LibreOffice 不可用，`.doc` 会以 `doc_converter_unavailable` 失败；`.docx`、`.xls` 和 `.xlsx` 不依赖 LibreOffice。

当前不处理 Excel 图表、图片和宏，也不支持密码保护文件。密码保护或损坏文件会产生稳定的预处理错误码，不会触发运行时联网安装或外部转换。
