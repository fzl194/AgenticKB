"""JWT-HS256 手写（stdlib，零依赖）。

钉死 HS256：decode 时 header.alg != "HS256" 一律拒（防 alg-confusion / "none"）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(signing_input: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()


def encode(payload: dict[str, Any], secret: str, *, ttl: int) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    body = {**payload, "iat": now, "exp": now + ttl}
    h = _b64encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    p = _b64encode(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{h}.{p}".encode("ascii")
    sig = _b64encode(_sign(signing_input, secret))
    return f"{h}.{p}.{sig}"


def decode(token: str, secret: str) -> dict[str, Any] | None:
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    h_b64, p_b64, sig_b64 = token.split(".")
    try:
        header = json.loads(_b64decode(h_b64))
        payload = json.loads(_b64decode(p_b64))
        sig = _b64decode(sig_b64)
    except Exception:
        return None
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        return None
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    expected = _sign(signing_input, secret)
    if not hmac.compare_digest(sig, expected):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or int(time.time()) >= exp:
        return None
    return payload
