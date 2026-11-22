"""Account administration from the command line, for bootstrapping and recovery.

    python -m ballot_box.accounts set-password USERNAME [--admin] [--email E] [--name N]
    python -m ballot_box.accounts grant IDENTITY {admin,manager}
    python -m ballot_box.accounts revoke IDENTITY {admin,manager}

``set-password`` reads the password from the terminal. ``IDENTITY`` is
``provider:external_id``; ``grant`` creates the account if it never logged
in, so a CAS user can be made an administrator before their first login.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from .auth import password as password_auth
from .config import get_settings
from .db import make_engine, make_sessionmaker
from .models import Account


def _set_role(account: Account, role: str, value: bool) -> None:
    if role == "admin":
        account.is_admin = value
    else:
        account.can_manage = value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ballot_box.accounts")
    sub = parser.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("set-password", help="create a password login, or change its password")
    sp.add_argument("username")
    sp.add_argument("--admin", action="store_true", help="also make the account an administrator")
    sp.add_argument("--email")
    sp.add_argument("--name")
    for name in ("grant", "revoke"):
        p = sub.add_parser(name, help=f"{name} a role")
        p.add_argument("identity", help="provider:external_id, e.g. cas:jdoe")
        p.add_argument("role", choices=["admin", "manager"])
    args = parser.parse_args(argv)

    db = make_sessionmaker(make_engine(get_settings().database_url))
    with db.begin() as session:
        if args.command == "set-password":
            password = getpass.getpass(f"password for {args.username}: ")
            if password != getpass.getpass("again: "):
                print("the passwords differ", file=sys.stderr)
                return 1
            try:
                password_auth.set_password(
                    session, args.username, password, email=args.email, display_name=args.name
                )
            except password_auth.WeakPassword as e:
                print(e, file=sys.stderr)
                return 1
            identity, role, value = f"password:{args.username}", "admin", True
            if not args.admin:
                print(f"set the password of {args.username}")
                return 0
        else:
            identity, role, value = args.identity, args.role, args.command == "grant"
        provider, sep, external_id = identity.partition(":")
        if not sep or not external_id:
            print("an identity is provider:external_id", file=sys.stderr)
            return 1
        account = session.scalar(
            select(Account).where(Account.provider == provider, Account.external_id == external_id)
        )
        if account is None:
            account = Account(provider=provider, external_id=external_id)
            session.add(account)
        _set_role(account, role, value)
    print(f"{identity}: {role} {'granted' if value else 'revoked'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
