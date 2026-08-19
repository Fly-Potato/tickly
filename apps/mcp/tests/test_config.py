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


def test_invalid_token_hash_error_does_not_echo_sensitive_input() -> None:
    sentinel = "mcp-token-config-sentinel-do-not-leak"

    with pytest.raises(ValidationError) as error:
        Settings(token_sha256=sentinel, _env_file=None)

    message = str(error.value)
    assert "token_sha256" in message
    assert "value_error" in message
    assert "input_value" not in message
    assert "input_type" not in message
    assert sentinel not in message


def test_production_missing_token_hash_does_not_echo_environment_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "mcp-config-environment-sentinel"
    monkeypatch.setenv("TICKLY_MCP_ENVIRONMENT", "production")
    monkeypatch.delenv("TICKLY_MCP_TOKEN_SHA256", raising=False)
    monkeypatch.setenv(
        "TICKLY_MCP_API_BASE_URL", f"https://{sentinel}.example.com"
    )

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    message = str(error.value)
    assert "production token_sha256 is required" in message
    assert "input_value" not in message
    assert "input_type" not in message
    assert sentinel not in message


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allowed_hosts", ""),
        ("allowed_hosts", "   "),
        ("allowed_hosts", "http://tickly.example.com"),
        ("allowed_hosts", "*.example.com"),
        ("allowed_hosts", "tickly.example.com/path"),
        ("allowed_hosts", "tickly.example.com:"),
        ("allowed_hosts", "tickly.example.com?"),
        ("allowed_hosts", "tickly.example.com#"),
        ("allowed_hosts", "tickly.\rexample.com"),
        ("allowed_hosts", "tickly.\nexample.com"),
        ("allowed_hosts", "tickly.\texample.com"),
        ("allowed_hosts", "[fe80::1%eth 0]:*"),
        ("allowed_hosts", "[fe80::1%eth\u0085]:*"),
        ("allowed_hosts", "[fe80::1%网卡]:*"),
        ("allowed_hosts", "[fe80::1%eth:0]:*"),
        ("allowed_hosts", "[fe80::1%eth!0]:*"),
        ("allowed_origins", ""),
        ("allowed_origins", "   "),
        ("allowed_origins", "tickly.example.com"),
        ("allowed_origins", "https://*.example.com"),
        ("allowed_origins", "https://tickly.example.com/path"),
        ("allowed_origins", "https://user@tickly.example.com"),
        ("allowed_origins", "https://tickly.example.com:"),
        ("allowed_origins", "https://tickly.example.com?"),
        ("allowed_origins", "https://tickly.example.com#"),
        ("allowed_origins", "https://tickly.\rexample.com"),
        ("allowed_origins", "https://tickly.\nexample.com"),
        ("allowed_origins", "https://tickly.\texample.com"),
        ("allowed_origins", "http://[fe80::1%eth 0]:*"),
        ("allowed_origins", "http://[fe80::1%eth\u0085]:*"),
        ("allowed_origins", "http://[fe80::1%网卡]:*"),
        ("allowed_origins", "http://[fe80::1%eth:0]:*"),
        ("allowed_origins", "http://[fe80::1%eth!0]:*"),
    ],
)
def test_transport_allowlists_reject_invalid_entries(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: [value]}, _env_file=None)


@pytest.mark.parametrize(
    ("field", "values"),
    [
        (
            "allowed_hosts",
            [
                "tickly.example.com",
                "xn--fiqs8s.example",
                "tickly.example.com:443",
                "127.0.0.1:*",
                "[::1]:*",
            ],
        ),
        (
            "allowed_origins",
            [
                "https://tickly.example.com",
                "https://xn--fiqs8s.example",
                "https://tickly.example.com:443",
                "http://127.0.0.1:*",
                "http://[::1]:*",
            ],
        ),
    ],
)
def test_transport_allowlists_accept_sdk_patterns(
    field: str, values: list[str]
) -> None:
    settings = Settings(**{field: values}, _env_file=None)

    assert getattr(settings, field) == values


@pytest.mark.parametrize("field", ["connect_timeout_seconds", "request_timeout_seconds"])
def test_timeouts_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: 0}, _env_file=None)
