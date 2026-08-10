from __future__ import annotations

import base64
import json

from main_control_service.jwt_util import decode, encode


def test_roundtrip():
    token = encode({"sub": "alice", "role": "admin", "name": "Alice"}, "secret", ttl=60)
    payload = decode(token, "secret")
    assert payload is not None
    assert payload["sub"] == "alice"
    assert payload["role"] == "admin"
    assert payload["name"] == "Alice"
    assert "exp" in payload and "iat" in payload


def test_decode_expired_returns_none():
    token = encode({"sub": "a"}, "secret", ttl=-10)
    assert decode(token, "secret") is None


def test_decode_wrong_secret_returns_none():
    token = encode({"sub": "a"}, "secret", ttl=60)
    assert decode(token, "other") is None


def test_decode_tampered_payload_returns_none():
    token = encode({"sub": "a"}, "secret", ttl=60)
    parts = token.split(".")
    payload = base64.urlsafe_b64decode(parts[1] + "==")
    tampered = payload.replace(b'"a"', b'"b"')
    parts[1] = base64.urlsafe_b64encode(tampered).rstrip(b"=").decode()
    assert decode(".".join(parts), "secret") is None


def test_decode_alg_none_rejected():
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "a"}).encode()).rstrip(b"=").decode()
    forged = f"{header}.{payload}."
    assert decode(forged, "secret") is None


def test_decode_garbage_returns_none():
    assert decode("not.a.jwt", "secret") is None
    assert decode("", "secret") is None
    assert decode("only.two", "secret") is None
