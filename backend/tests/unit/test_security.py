from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("password123")
    assert h != "password123"
    assert verify_password("password123", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip():
    token = create_access_token(1, "admin")
    payload = decode_access_token(token)
    assert payload["sub"] == "1"
    assert payload["role"] == "admin"
    assert payload["type"] == "access"


def test_refresh_token_hash():
    t = generate_refresh_token()
    assert t != hash_refresh_token(t)
    assert len(hash_refresh_token(t)) == 64  # sha256 hex
