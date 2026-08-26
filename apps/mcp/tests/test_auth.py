"""MCP Bearer 凭据解析的安全边界测试。"""

import pytest

from app.auth import token_from_authorization


RAW_TOKEN = "tickly-secret"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("Basic credentials", None),
        ("Bearer", None),
        ("Bearer ", None),
        ("Bearer  tickly-secret", None),
        ("Bearer\ttickly-secret", None),
        ("Bearer tickly-secret extra", None),
        (" Bearer tickly-secret", None),
        ("Bearer tickly-secret ", None),
        ("Bearer tickly\x00secret", None),
        ("Bearer tickly\x7fsecret", None),
        ("Bearer tickly\u0080secret", None),
        (f"Bearer {'x' * 513}", None),
        ("Bearer tickly-secret", RAW_TOKEN),
        ("Bearer !safe-invalid~", "!safe-invalid~"),
        ("bearer tickly-secret", RAW_TOKEN),
    ],
)
def test_token_from_authorization_accepts_only_bearer(
    value: str | None, expected: str | None
) -> None:
    assert token_from_authorization(value) == expected
