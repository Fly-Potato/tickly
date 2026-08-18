import pytest
from pydantic import ValidationError

from app.config import Environment, Settings


VALID_HASH = "a" * 64


def test_defaults_are_local_and_use_a_distinct_port() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert str(settings.host) == "127.0.0.1"
    assert settings.port == 8322
    assert str(settings.api_base_url) == "http://127.0.0.1:8321"
    assert settings.token_sha256 is None
    assert settings.allowed_hosts == ["127.0.0.1:*", "localhost:*"]


@pytest.mark.parametrize("value", ["", "abc", "g" * 64, "a" * 63, "a" * 65])
def test_token_hash_must_be_lowercase_sha256(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(token_sha256=value, _env_file=None)


def test_production_requires_token_hash_and_transport_allowlists() -> None:
    with pytest.raises(ValidationError):
        Settings(environment=Environment.PRODUCTION, _env_file=None)

    settings = Settings(
        environment=Environment.PRODUCTION,
        token_sha256=VALID_HASH,
        allowed_hosts=["tickly.example.com"],
        allowed_origins=["https://tickly.example.com"],
        _env_file=None,
    )
    assert settings.token_sha256 is not None


@pytest.mark.parametrize("field", ["connect_timeout_seconds", "request_timeout_seconds"])
def test_timeouts_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: 0}, _env_file=None)
