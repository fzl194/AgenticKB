from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class OfflineDeploymentContractTests(unittest.TestCase):
    def test_docker_context_excludes_offline_image_artifacts(self) -> None:
        dockerignore = _read(".dockerignore").splitlines()

        self.assertIn("cmkb*.tar", dockerignore)
        self.assertIn("cmkb*.tar.zst", dockerignore)
        self.assertIn("*.zst", dockerignore)
        self.assertIn("*.sha256", dockerignore)


    def test_runtime_image_does_not_embed_production_env(self) -> None:
        dockerfile = _read("docker/Dockerfile")

        self.assertIn("COPY .env.example ./", dockerfile)
        self.assertNotIn("COPY .env .env.example ./", dockerfile)


    def test_image_build_validates_document_dependencies_with_chinese_messages(self) -> None:
        dockerfile = _read("docker/Dockerfile")

        self.assertIn("command -v libreoffice", dockerfile)
        self.assertIn("libreoffice --headless --version", dockerfile)
        self.assertIn("import openpyxl, xlrd", dockerfile)
        self.assertIn("错误：未找到 LibreOffice", dockerfile)
        # bcc5636 起构建期验证扩为整条解析链（openpyxl/xlrd 在内），成功
        # 文案从「Excel 解析依赖验证通过」改为「解析链依赖验证通过」。
        self.assertIn("解析链依赖验证通过", dockerfile)


    def test_offline_build_is_versioned_compressed_and_checksummed(self) -> None:
        script = _read("deploy-build.sh")

        self.assertIn("set -Eeuo pipefail", script)
        self.assertIn("git rev-parse --short HEAD", script)
        self.assertIn("cmkb-${VERSION}.tar.zst", script)
        self.assertIn("docker run --rm", script)
        self.assertIn("command -v libreoffice", script)
        self.assertIn("import openpyxl, xlrd", script)
        self.assertIn("zstd -T0 -19", script)
        self.assertIn("sha256sum", script)


    def test_offline_deploy_checks_integrity_before_loading_and_validates_image(self) -> None:
        script = _read("deploy-server.sh")

        checksum_position = script.index('sha256sum -c "$CHECKSUM_FILE"')
        load_position = script.index('zstd -dc "$IMAGE_ARCHIVE" | docker load')
        dependency_position = script.index('docker run --rm "$IMAGE_NAME"')

        self.assertLess(checksum_position, load_position)
        self.assertLess(load_position, dependency_position)
        self.assertIn("离线镜像校验失败，请重新传输", script)
        self.assertIn("错误：镜像内未安装 LibreOffice", script)
        self.assertIn("import openpyxl, xlrd", script)


    def test_offline_delivery_documentation_defines_team_artifact_contract(self) -> None:
        documentation = _read("docs/deployment/offline-document-dependencies.md")

        self.assertIn("cmkb-2026.08.10.tar.zst", documentation)
        self.assertIn("SHA-256", documentation)
        self.assertIn("生产 `.env`", documentation)
        self.assertIn("共享盘", documentation)


if __name__ == "__main__":
    unittest.main()
