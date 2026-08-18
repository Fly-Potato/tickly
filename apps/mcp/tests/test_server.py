"""MCP Uvicorn 启动入口测试。"""

from typing import Any

import pytest

from app.config import Environment, Settings
from app import server


def test_run_server_passes_validated_settings_to_uvicorn(monkeypatch: Any) -> None:
    settings = Settings(
        environment=Environment.TEST,
        host="0.0.0.0",
        port=9444,
        token_sha256="a" * 64,
        _env_file=None,
    )
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(server, "Settings", lambda: settings)
    monkeypatch.setattr(
        server.uvicorn,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    server.run_server(reload=True)

    assert calls == [
        (
            ("app.main:app",),
            {"host": "0.0.0.0", "port": 9444, "reload": True},
        )
    ]


def test_main_accepts_only_reload_flag(monkeypatch: Any) -> None:
    reload_values: list[bool] = []
    monkeypatch.setattr(
        server, "run_server", lambda *, reload=False: reload_values.append(reload)
    )

    server.main(["--reload"])

    assert reload_values == [True]
    with pytest.raises(SystemExit) as raised:
        server.main(["--unknown"])
    assert raised.value.code == 2
