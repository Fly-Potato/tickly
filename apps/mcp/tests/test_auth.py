"""MCP 静态 Bearer 认证的安全边界测试。"""

import hashlib

import pytest

from app.auth import bearer_matches, token_from_authorization


RAW_TOKEN = "tickly-secret"
TOKEN_SHA256 = hashlib.sha256(RAW_TOKEN.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("Basic credentials", None),
        ("Bearer", None),
        ("Bearer ", None),
        ("Bearer tickly-secret", RAW_TOKEN),
        ("bearer tickly-secret", RAW_TOKEN),
    ],
)
def test_token_from_authorization_accepts_only_bearer(
    value: str | None, expected: str | None
) -> None:
    assert token_from_authorization(value) == expected


def test_bearer_matches_sha256_digest() -> None:
    assert bearer_matches(RAW_TOKEN, TOKEN_SHA256) is True
    assert bearer_matches("wrong", TOKEN_SHA256) is False


@pytest.mark.parametrize("expected_sha256", [None, ""])
def test_bearer_fails_closed_without_a_configured_digest(
    expected_sha256: str | None,
) -> None:
    assert bearer_matches(RAW_TOKEN, expected_sha256) is False
