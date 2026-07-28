"""Tickly 后端维护命令行入口。"""

import argparse
import getpass
import sys
from collections.abc import Sequence

from app.core.config import Settings
from app.core.security import InvalidPassword, InvalidUsername, normalize_username
from app.db.session import create_engine_for_settings, create_session_factory
from app.services.accounts import (
    AccountAlreadyExists,
    AccountNotFound,
    change_password,
    create_account,
    deactivate_account,
    revoke_all_sessions,
)


class PasswordConfirmationMismatch(Exception):
    """两次交互式密码输入不一致。"""


class UsernameConfirmationMismatch(Exception):
    """停用账号时输入的确认用户名不匹配。"""


def main(argv: Sequence[str] | None = None) -> int:
    """解析并执行维护命令，只向终端输出不含凭据的稳定结果。"""

    arguments = _build_parser().parse_args(argv)
    settings = Settings()
    database_engine = create_engine_for_settings(settings)
    session_factory = create_session_factory(database_engine)

    try:
        with session_factory() as session:
            if arguments.command == "create":
                password = _read_confirmed_password()
                user = create_account(session, arguments.username, password)
                print(f"账号已创建：{user.username}")
            elif arguments.command == "change-password":
                password = _read_confirmed_password()
                user = change_password(session, arguments.username, password)
                print(f"密码已修改：{user.username}")
            elif arguments.command == "deactivate":
                normalized = normalize_username(arguments.username)
                confirmation = input("请输入完整用户名确认停用：")
                if confirmation != normalized:
                    raise UsernameConfirmationMismatch
                user = deactivate_account(session, normalized)
                print(f"账号已停用：{user.username}")
            else:
                revoked_count = revoke_all_sessions(session, arguments.username)
                print(f"已撤销 {revoked_count} 个会话")
    except PasswordConfirmationMismatch:
        print("错误：两次输入的密码不一致", file=sys.stderr)
        return 1
    except UsernameConfirmationMismatch:
        print("错误：确认用户名不匹配", file=sys.stderr)
        return 1
    except AccountAlreadyExists:
        print("错误：当前只能创建一个账号", file=sys.stderr)
        return 1
    except AccountNotFound:
        print("错误：账号不存在", file=sys.stderr)
        return 1
    except (InvalidUsername, InvalidPassword) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    finally:
        # CLI 显式拥有 Engine 生命周期，避免一次性维护进程遗留连接和文件锁。
        database_engine.dispose()

    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tickly 后端维护命令")
    groups = parser.add_subparsers(dest="group", required=True)
    user_parser = groups.add_parser("user", help="维护唯一账号")
    commands = user_parser.add_subparsers(dest="command", required=True)

    for command, help_text in (
        ("create", "创建账号"),
        ("change-password", "修改密码"),
        ("deactivate", "停用账号"),
        ("revoke-sessions", "撤销全部会话"),
    ):
        command_parser = commands.add_parser(command, help=help_text)
        command_parser.add_argument("--username", required=True, help="登录用户名")

    return parser


def _read_confirmed_password() -> str:
    password = getpass.getpass("请输入密码：")
    confirmation = getpass.getpass("请再次输入密码：")
    if password != confirmation:
        raise PasswordConfirmationMismatch
    return password


if __name__ == "__main__":
    raise SystemExit(main())
