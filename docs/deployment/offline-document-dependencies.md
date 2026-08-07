# Word 与 Excel 离线部署依赖

知识挖掘支持 `.doc`、`.docx`、`.xls` 和 `.xlsx`。`.doc` 转换使用本机 LibreOffice；Excel 读取使用 Python 包 `openpyxl` 和 `xlrd`。运行时不会下载依赖，也不会调用任何云端文档转换服务。

## 推荐方式：交付完整 Docker 镜像

在与生产环境架构一致、可联网的 Linux 构建机上执行：

```bash
bash deploy-build.sh
```

生成的 `cmkb.tar` 已包含 LibreOffice Writer、`openpyxl` 和 `xlrd`。复制到离线服务器后执行：

```bash
docker load -i cmkb.tar
bash deploy-server.sh
```

## 非 Docker Python 服务

在可联网、Python 版本及 CPU 架构与生产一致的机器上准备 wheelhouse：

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

## LibreOffice 离线安装

不使用项目导出的 Docker 镜像时，需要提前下载与目标 Linux 发行版、版本和 CPU 架构匹配的 LibreOffice DEB/RPM 包及其全部依赖。推荐使用发行版官方离线仓库或在同版本联网机器上生成依赖包集合，不要把其他发行版的包直接混装。服务只需要 Writer/无界面转换能力，不依赖桌面会话。

## 上线检查

```bash
python -c "import openpyxl, xlrd; print(openpyxl.__version__, xlrd.__version__)"
command -v soffice || command -v libreoffice
```

若第二条命令没有输出，`.doc` 会以 `doc_converter_unavailable` 失败，并通过运行文档 API 暴露诊断；`.docx`、`.xls` 和 `.xlsx` 不受影响。

当前不处理 Excel 图表、图片和宏，也不支持密码保护文件。密码保护或损坏文件会产生稳定的预处理错误码，不会触发运行时联网安装或外部转换。
