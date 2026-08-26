import pytest
from pydantic import ValidationError

from app.config import Environment, Settings


def test_defaults_are_local_and_use_a_distinct_port() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert str(settings.host) == "127.0.0.1"
    assert settings.port == 8322
    assert str(settings.api_base_url) == "http://127.0.0.1:8321"
    assert not hasattr(settings, "token_sha256")
    assert settings.allowed_hosts == ["127.0.0.1:*", "localhost:*"]


def test_legacy_token_hash_environment_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧静态摘要变量必须被忽略，且不能重新出现在 Settings 表面。"""
    monkeypatch.setenv("TICKLY_MCP_TOKEN_SHA256", "legacy-static-digest")

    settings = Settings(_env_file=None)

    assert "token_sha256" not in type(settings).model_fields
    assert "token_sha256" not in settings.model_dump()


def test_production_requires_transport_allowlists_but_not_static_token_hash() -> None:
    """生产配置只保留传输白名单，用户 PAT 由 API 动态验证。"""
    with pytest.raises(ValidationError):
        Settings(
            environment=Environment.PRODUCTION,
            allowed_hosts=[],
            allowed_origins=[],
            _env_file=None,
        )

    settings = Settings(
        environment=Environment.PRODUCTION,
        allowed_hosts=["tickly.example.com"],
        allowed_origins=["https://tickly.example.com"],
        _env_file=None,
    )
    assert settings.environment is Environment.PRODUCTION


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


@pytest.mark.parametrize(
    "value",
    [
        "",
        "contains space",
        "X-Request-ID\r\nInjected",
        "X:Request-ID",
        "X-请求-ID",
        "Authorization",
        "authorization",
        "Host",
        "Origin",
        "Content-Type",
        "Accept",
        "MCP-Protocol-Version",
        "MCP-Session-ID",
        "connection",
        "CoNnEcTiOn",
        "content-length",
        "CoNtEnT-LeNgTh",
        "transfer-encoding",
        "TrAnSfEr-EnCoDiNg",
        "te",
        "TE",
        "trailer",
        "TrAiLeR",
        "upgrade",
        "UpGrAdE",
        "www-authenticate",
        "WwW-AuThEnTiCaTe",
    ],
)
def test_request_id_header_rejects_invalid_or_reserved_names(value: str) -> None:
    """关联头不得覆盖凭据、消息分帧或 MCP transport 头。"""
    with pytest.raises(ValidationError):
        Settings(request_id_header=value, _env_file=None)


def test_request_id_header_error_does_not_echo_input() -> None:
    """配置错误不得回显输入，避免未来误把敏感值拼入关联头配置。"""
    sentinel = "mcp-config-header-sentinel-do-not-leak"

    with pytest.raises(ValidationError) as error:
        Settings(
            request_id_header=f"X-Request-ID\r\n{sentinel}",
            _env_file=None,
        )

    message = str(error.value)
    assert "request_id_header" in message
    assert "input_value" not in message
    assert "input_type" not in message
    assert sentinel not in message


def test_request_id_header_accepts_rfc_token_name() -> None:
    settings = Settings(request_id_header="X-Correlation-ID", _env_file=None)

    assert settings.request_id_header == "X-Correlation-ID"
