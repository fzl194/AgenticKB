from __future__ import annotations

from knowledge_mining.mining.kb.security import hash_password, verify_password


def test_hash_password_format():
    h = hash_password("hunter2")
    parts = h.split("$")
    assert parts[0] == "pbkdf2_sha256"
    assert parts[1].isdigit() and int(parts[1]) >= 100_000
    assert parts[2]  # salt
    assert parts[3]  # hash


def test_verify_password_correct():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True


def test_verify_password_wrong():
    h = hash_password("right")
    assert verify_password("wrong", h) is False


def test_verify_password_garbled_format_returns_false():
    assert verify_password("x", "not-a-valid-format") is False
    assert verify_password("x", "pbkdf2_sha256$abc$no$no") is False


def test_hash_password_unique_salt():
    """两次哈希同密码应得不同结果（随机盐），但都能验过。"""
    a, b = hash_password("same"), hash_password("same")
    assert a != b
    assert verify_password("same", a) and verify_password("same", b)
