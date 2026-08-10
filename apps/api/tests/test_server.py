from importlib import import_module

import pytest


@pytest.mark.parametrize(
    ("arguments", "expected_reload"),
    [([], False), (["--reload"], True)],
    ids=["生产模式", "开发热重载"],
)
def test_server_passes_listener_settings_to_uvicorn(
    arguments: list[str],
    expected_reload: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = import_module("app.server")
    captured: dict[str, object] = {}

    def fake_run(application: str, **options: object) -> None:
        captured["application"] = application
        captured.update(options)

    monkeypatch.setattr(server.uvicorn, "run", fake_run)
    monkeypatch.setenv("TICKLY_HOST", "0.0.0.0")
    monkeypatch.setenv("TICKLY_PORT", "9100")

    server.main(arguments)

    assert captured == {
        "application": "app.main:app",
        "host": "0.0.0.0",
        "port": 9100,
        "reload": expected_reload,
    }
