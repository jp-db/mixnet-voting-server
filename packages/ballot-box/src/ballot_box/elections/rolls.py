"""Voter rolls and manager lists from CSV.

Voters, one per row: ``identity,email,display_name``, where the identity is
``provider:external_id`` (``cas:jdoe``, ``password:alice``) and the other
two columns are optional. A header row is optional too; with one, the
columns may come in any order and the identity may instead be split into
``provider`` and ``external_id`` columns::

    identity,email,display_name
    cas:jdoe,jdoe@example.edu,Jane Doe
    "cas:rroe",,"Roe, Richard"

Managers: one identity per row, in the first (or the ``identity``) column.
"""

from __future__ import annotations

import csv
import io

from .service import Invalid, VoterEntry

PROVIDERS = frozenset({"password", "cas"})
"""The login methods an identity can belong to."""

MAX_ROWS = 100_000

_ALIASES = {
    "identity": "identity",
    "login": "identity",
    "provider": "provider",
    "external_id": "external_id",
    "username": "external_id",
    "user": "external_id",
    "email": "email",
    "mail": "email",
    "display_name": "display_name",
    "name": "display_name",
}


def parse_identity(text: str, where: str = "") -> tuple[str, str]:
    """``provider:external_id`` -> its two parts."""
    provider, sep, external_id = text.strip().partition(":")
    prefix = f"{where}: " if where else ""
    if not sep or not provider or not external_id.strip():
        raise Invalid(f"{prefix}“{text}” is not an identity: write provider:username, e.g. cas:jdoe")
    if provider not in PROVIDERS:
        raise Invalid(f"{prefix}unknown login method “{provider}” (known: {', '.join(sorted(PROVIDERS))})")
    if len(external_id) > 255:
        raise Invalid(f"{prefix}the username is too long")
    return provider, external_id.strip()


def _rows(text: str) -> list[tuple[int, list[str]]]:
    if text.startswith("﻿"):  # spreadsheets like to start CSV files with a byte order mark
        text = text[1:]
    try:
        sample = text[:4096]
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for n, row in enumerate(csv.reader(io.StringIO(text), dialect), start=1):
        cells = [c.strip() for c in row]
        if not any(cells) or cells[0].startswith("#"):
            continue
        rows.append((n, cells))
        if len(rows) > MAX_ROWS:
            raise Invalid(f"more than {MAX_ROWS} rows")
    return rows


def _header(cells: list[str]) -> dict[str, int] | None:
    names = [_ALIASES.get(c.lower().replace(" ", "_")) for c in cells]
    if "identity" not in names and not {"provider", "external_id"} <= set(names):
        return None
    return {name: i for i, name in enumerate(names) if name is not None}


def _cell(cells: list[str], i: int | None) -> str:
    return cells[i] if i is not None and i < len(cells) else ""


def parse_voters(text: str) -> list[VoterEntry]:
    rows = _rows(text)
    header = _header(rows[0][1]) if rows else None
    if header is not None:
        rows = rows[1:]
    columns = header or {"identity": 0, "email": 1, "display_name": 2}
    out = []
    for n, cells in rows:
        where = f"line {n}"
        if header is None and len(cells) > 3:
            raise Invalid(f"{where}: too many columns (put a name that has a comma in quotes)")
        if "identity" in columns:
            provider, external_id = parse_identity(_cell(cells, columns["identity"]), where)
        else:
            provider, external_id = parse_identity(
                f"{_cell(cells, columns['provider'])}:{_cell(cells, columns['external_id'])}", where
            )
        email = _cell(cells, columns.get("email")) or None
        name = _cell(cells, columns.get("display_name")) or None
        if email is not None and len(email) > 320:
            raise Invalid(f"{where}: the email is too long")
        if name is not None and len(name) > 200:
            raise Invalid(f"{where}: the name is too long")
        out.append(VoterEntry(provider, external_id, email, name))
    if not out:
        raise Invalid("no voters in the file")
    return out


def parse_identities(text: str) -> list[tuple[str, str]]:
    rows = _rows(text)
    column = 0
    if rows:
        columns = _header(rows[0][1])
        if columns is not None:
            rows = rows[1:]
            if "identity" not in columns:
                p, x = columns["provider"], columns["external_id"]
                return [parse_identity(f"{_cell(c, p)}:{_cell(c, x)}", f"line {n}") for n, c in rows]
            column = columns["identity"]
    out = [parse_identity(_cell(cells, column), f"line {n}") for n, cells in rows]
    if not out:
        raise Invalid("no identities in the file")
    return out
