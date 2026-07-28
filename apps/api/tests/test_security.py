from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.security import (
    InvalidPassword,
    InvalidToken,
    InvalidUsername,
    decode_token,
    digest_refresh_token,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    normalize_username,
    refresh_digest_matches,
    validate_password,
    verify_dummy_password,
    verify_password,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(jwt_secret="s" * 64, _env_file=None)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [(" Potato ", "potato"), ("user_01", "user_01"), ("a-b", "a-b")],
)
def test_normalize_username(raw: str, normalized: str) -> None:
    assert normalize_username(raw) == normalized


@pytest.mark.parametrize("raw", ["ab", "has space", "中文名", "a" * 33])
def test_normalize_username_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(InvalidUsername):
        normalize_username(raw)


def test_password_hash_round_trip() -> None:
    encoded = hash_password("correct horse battery staple")

    assert encoded.startswith("$argon2")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)


def test_password_requires_at_least_twelve_characters() -> None:
    with pytest.raises(InvalidPassword):
        validate_password("a" * 11)

    assert validate_password("a" * 12) == "a" * 12


def test_dummy_password_verification_does_not_expose_a_result() -> None:
    assert verify_dummy_password("unknown password") is None


def test_access_and_refresh_tokens_enforce_type_and_sid(settings: Settings) -> None:
    access = issue_access_token("user-id", settings)
    refresh = issue_refresh_token("user-id", "session-id", settings)

    assert decode_token(access, "access", settings).sub == "user-id"
    assert decode_token(refresh, "refresh", settings).sid == "session-id"
    with pytest.raises(InvalidToken):
        decode_token(access, "refresh", settings)


def test_refresh_token_can_use_a_fixed_absolute_expiry(settings: Settings) -> None:
    expires_at = datetime.now(UTC) + timedelta(days=3)
    token = issue_refresh_token(
        "user-id", "session-id", settings, expires_at=expires_at
    )

    payload = decode_token(token, "refresh", settings)

    assert payload.exp == expires_at.replace(microsecond=0)


def test_expired_token_is_rejected(settings: Settings) -> None:
    token = issue_refresh_token(
        "user-id",
        "session-id",
        settings,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    with pytest.raises(InvalidToken):
        decode_token(token, "refresh", settings)


@pytest.mark.parametrize(
    ("overrides", "removed_claim"),
    [
        ({"iss": "another-issuer"}, None),
        ({"aud": "another-audience"}, None),
        ({}, "sub"),
        ({}, "jti"),
        ({}, "type"),
        ({}, "iat"),
        ({}, "exp"),
    ],
)
def test_invalid_or_missing_required_claims_are_rejected(
    settings: Settings,
    overrides: dict[str, str],
    removed_claim: str | None,
) -> None:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "user-id",
        "jti": "token-id",
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    if removed_claim is not None:
        claims.pop(removed_claim)
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    with pytest.raises(InvalidToken):
        decode_token(token, "access", settings)


def test_wrong_signature_is_rejected(settings: Settings) -> None:
    other_settings = Settings(
        jwt_secret="d" * 64, _env_file=None
    )
    token = issue_access_token("user-id", other_settings)

    with pytest.raises(InvalidToken):
        decode_token(token, "access", settings)


def test_algorithm_is_restricted_to_the_configured_allowlist(settings: Settings) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "user-id",
            "jti": "token-id",
            "type": "access",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm="HS384",
    )

    with pytest.raises(InvalidToken):
        decode_token(token, "access", settings)


def test_refresh_token_requires_session_id(settings: Settings) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "user-id",
            "jti": "token-id",
            "type": "refresh",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(InvalidToken):
        decode_token(token, "refresh", settings)


def test_refresh_digest_is_sha256_and_uses_constant_time_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def record_compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr("app.core.security.hmac.compare_digest", record_compare)
    digest = digest_refresh_token("refresh-token")

    assert digest == (
        "0eb17643d4e9261163783a420859c92c7d212fa9624106a12b510afbec266120"
    )
    assert refresh_digest_matches("refresh-token", digest)
    assert calls == [(digest, digest)]
