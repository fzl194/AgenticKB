"""LLM 管理面只保留仍有产品入口的运维能力。"""

from llm_service.api.admin import router


def test_manual_config_reload_route_is_removed():
    paths = {route.path for route in router.routes}
    assert "/api/v1/admin/reload-config" not in paths
    assert "/api/v1/admin/worker-status" in paths
    assert "/api/v1/admin/cleanup" in paths
