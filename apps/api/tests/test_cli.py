from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.cli import main


PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "another correct password"


@pytest.fixture
def cli_database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    database_path = tmp_path / "cli.db"
    database_url = f"sqlite:///{database_path}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")
    monkeypatch.setenv("TICKLY_DATABASE_URL", database_url)
    monkeypatch.setenv("TICKLY_ENVIRONMENT", "test")
    return database_url


def set_password_answers(
    monkeypatch: pytest.MonkeyPatch, *answers: str
) -> None:
    values = iter(answers)
    monkeypatch.setattr("getpass.getpass", lambda _: next(values))


def test_create_cli_reads_password_twice(
    monkeypatch: pytest.MonkeyPatch,
    cli_database_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_password_answers(monkeypatch, PASSWORD, PASSWORD)

    assert main(["user", "create", "--username", "Potato"]) == 0

    captured = capsys.readouterr()
    assert "账号已创建" in captured.out
    assert PASSWORD not in captured.out + captured.err
    assert cli_database_url not in captured.out + captured.err


def test_create_cli_rejects_mismatched_password_without_writing_account(
    monkeypatch: pytest.MonkeyPatch,
    cli_database_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_password_answers(monkeypatch, PASSWORD, NEW_PASSWORD)
    assert main(["user", "create", "--username", "potato"]) != 0

    set_password_answers(monkeypatch, PASSWORD, PASSWORD)
    assert main(["user", "create", "--username", "potato"]) == 0
    captured = capsys.readouterr()
    assert "两次输入的密码不一致" in captured.err
    assert "账号已创建" in captured.out


def test_create_cli_refuses_a_second_account(
    monkeypatch: pytest.MonkeyPatch,
    cli_database_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_password_answers(monkeypatch, PASSWORD, PASSWORD, NEW_PASSWORD, NEW_PASSWORD)

    assert main(["user", "create", "--username", "potato"]) == 0
    assert main(["user", "create", "--username", "second"]) != 0

    captured = capsys.readouterr()
    assert "只能创建一个账号" in captured.err
    assert PASSWORD not in captured.out + captured.err
    assert NEW_PASSWORD not in captured.out + captured.err
    assert cli_database_url not in captured.out + captured.err


def test_change_password_cli_reads_and_confirms_new_password(
    monkeypatch: pytest.MonkeyPatch,
    cli_database_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_password_answers(monkeypatch, PASSWORD, PASSWORD)
    assert main(["user", "create", "--username", "potato"]) == 0
    set_password_answers(monkeypatch, NEW_PASSWORD, NEW_PASSWORD)

    assert main(["user", "change-password", "--username", "Potato"]) == 0

    captured = capsys.readouterr()
    assert "密码已修改" in captured.out
    assert NEW_PASSWORD not in captured.out + captured.err


def test_deactivate_cli_requires_the_full_normalized_username(
    monkeypatch: pytest.MonkeyPatch,
    cli_database_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_password_answers(monkeypatch, PASSWORD, PASSWORD)
    assert main(["user", "create", "--username", "potato"]) == 0
    monkeypatch.setattr("builtins.input", lambda _: "wrong")
    assert main(["user", "deactivate", "--username", "Potato"]) != 0
    monkeypatch.setattr("builtins.input", lambda _: "potato")

    assert main(["user", "deactivate", "--username", "Potato"]) == 0

    captured = capsys.readouterr()
    assert "确认用户名不匹配" in captured.err
    assert "账号已停用" in captured.out


def test_revoke_sessions_cli_reports_a_safe_result(
    monkeypatch: pytest.MonkeyPatch,
    cli_database_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_password_answers(monkeypatch, PASSWORD, PASSWORD)
    assert main(["user", "create", "--username", "potato"]) == 0

    assert main(["user", "revoke-sessions", "--username", "potato"]) == 0

    captured = capsys.readouterr()
    assert "已撤销 0 个会话" in captured.out
    assert cli_database_url not in captured.out + captured.err
