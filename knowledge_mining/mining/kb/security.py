"""密码哈希 —— PBKDF2-HMAC-SHA256（stdlib，零依赖）。

格式：``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``。
验签用 ``hmac.compare_digest`` 恒定时间比较，防时序侧信道。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_ITERATIONS = 200_000  # OWASP 2023 量级
_ALGO = "pbkdf2_sha256"
_DIGEST = "sha256"


def hash_password(plain: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(_DIGEST, plain.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(derived).decode()}"


def verify_password(plain: str, stored: str) -> bool:
    if not isinstance(stored, str):
        return False
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != _ALGO:
        return False
    try:
        iterations = int(parts[1])
        salt = base64.b64decode(parts[2])
        expected = base64.b64decode(parts[3])
    except (ValueError, Exception):  # noqa: BLE001 — binascii.Error 是 ValueError 子类
        return False
    if iterations < 1 or not salt or not expected:
        return False
    derived = hashlib.pbkdf2_hmac(_DIGEST, plain.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)
