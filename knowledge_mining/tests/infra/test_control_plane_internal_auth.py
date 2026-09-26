from knowledge_mining.mining.infra import control_plane


class _Response:
    text = "value: ok\n"

    def raise_for_status(self) -> None:
        return None


def test_raw_config_request_sends_internal_auth(monkeypatch) -> None:
    captured = {}

    def fake_get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _Response()

    monkeypatch.setattr(control_plane, "control_plane_internal_headers", lambda: {
        "X-Internal-Auth": "bootstrap-secret",
    })
    monkeypatch.setattr(control_plane.httpx, "get", fake_get)

    assert control_plane._get_raw("database") == {"value": "ok"}
    assert captured["headers"] == {"X-Internal-Auth": "bootstrap-secret"}
