"""系统配置（system/*.yaml）读写通路测试。

聚焦 ``site`` 品牌块的 GET(JSON) / GET(raw) / PUT(raw) 通路。这些路由是通用系统配置
CRUD，``site`` 只是 ``ui.yaml`` 里的内容，因此后端零代码改动即支持品牌可配。
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from main_control_service.main import create_app

_UI_YAML = """\
site:
  title: CoreMasterKB
  name: CoreMaster
  badge: Knowledge Base
  logo_text: KB
  icon: ""
mining_api_base: http://localhost:8901
"""


def _client(tmp_path: Path) -> TestClient:
    """构造一个指向临时 config 目录的 main_control 测试客户端。"""
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "ui.yaml").write_text(_UI_YAML, encoding="utf-8")
    # 用上下文管理器触发 lifespan（建/拆 proxy client）。
    return TestClient(create_app(config_dir=tmp_path))


def test_get_system_ui_returns_site_block(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.get("/api/v1/system/ui")
        assert r.status_code == 200
        site = r.json()["site"]
        assert site["title"] == "CoreMasterKB"
        assert site["name"] == "CoreMaster"
        assert site["badge"] == "Knowledge Base"
        assert site["logo_text"] == "KB"
        assert site["icon"] == ""
        # 其余键被通用通路原样返回（不被 site 影响）。
        assert r.json()["mining_api_base"] == "http://localhost:8901"


def test_put_system_ui_raw_updates_site_and_preserves_other_keys(tmp_path: Path) -> None:
    new_yaml = (
        "site:\n"
        "  title: 我的知识库\n"
        "  name: MyKB\n"
        "  badge: KB\n"
        "  logo_text: MK\n"
        '  icon: "data:image/svg+xml;base64,e30="\n'
        "mining_api_base: http://localhost:8901\n"
    )
    with _client(tmp_path) as c:
        r = c.put(
            "/api/v1/system/ui/raw",
            content=new_yaml,
            headers={"Content-Type": "text/yaml"},
        )
        assert r.status_code == 200, r.text
        site = c.get("/api/v1/system/ui").json()["site"]
        assert site["title"] == "我的知识库"
        assert site["name"] == "MyKB"
        assert site["icon"].startswith("data:image/svg+xml")
        # 其它键保留（PUT 是整文件覆写，由调用方保证完整；这里验证通路不丢非 site 键）。
        assert c.get("/api/v1/system/ui").json()["mining_api_base"] == "http://localhost:8901"


def test_get_system_config_not_found(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.get("/api/v1/system/does-not-exist")
        assert r.status_code == 404
