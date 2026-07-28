import pytest
from pydantic import ValidationError

from app.core.config import Environment, Settings


def test_default_settings_are_for_local_development() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.app_name == "Tickly API"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.log_level == "INFO"
    assert settings.log_json is False
    assert settings.request_id_header == "X-Request-ID"
    assert settings.database_url == "sqlite:///./data/tickly.db"


def test_database_url_can_be_overridden_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TICKLY_DATABASE_URL", "sqlite:////data/tickly.db")

    settings = Settings(_env_file=None)

    assert settings.database_url == "sqlite:////data/tickly.db"


def test_authentication_defaults_are_explicit() -> None:
    settings = Settings(_env_file=None)

    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_issuer == "tickly-api"
    assert settings.jwt_audience == "tickly-web"
    assert settings.access_token_minutes == 15
    assert settings.refresh_token_days == 30
    assert settings.refresh_cookie_name == "tickly_refresh"
    assert settings.refresh_cookie_secure is False


def test_production_rejects_development_authentication_settings() -> None:
    with pytest.raises(ValidationError):
        Settings(environment=Environment.PRODUCTION, _env_file=None)


def test_production_accepts_explicit_secure_authentication_settings() -> None:
    settings = Settings(
        environment=Environment.PRODUCTION,
        jwt_secret="a" * 32,
        refresh_cookie_secure=True,
        _env_file=None,
    )

    assert settings.environment is Environment.PRODUCTION


@pytest.mark.parametrize(
    ("field", "value"),
    [("access_token_minutes", 0), ("refresh_token_days", 0)],
)
def test_token_lifetimes_must_be_positive(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value}, _env_file=None)


def test_tickly_prefixed_environment_variables_are_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TICKLY_ENVIRONMENT", "test")
    monkeypatch.setenv("TICKLY_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("TICKLY_LOG_JSON", "true")

    settings = Settings(_env_file=None)

    assert settings.environment is Environment.TEST
    assert settings.log_level == "DEBUG"
    assert settings.log_json is True


@pytest.mark.parametrize("value", ["staging", "local", "prod"])
def test_invalid_environment_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(environment=value, _env_file=None)


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(log_level="TRACE", _env_file=None)


@pytest.mark.parametrize("value", ["api/v1", "/api/v1/", "/"])
def test_invalid_api_prefix_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(api_v1_prefix=value, _env_file=None)
