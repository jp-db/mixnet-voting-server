"""elections open to login methods, voters who join themselves

Replaces 0003's open_to_all (every registered account, at freeze) with
open_to (every user of the listed login methods, at any time).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-23 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("elections") as batch:
        batch.add_column(sa.Column("open_to", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    # Every registered account was in practice password or cas logins.
    op.execute('UPDATE elections SET open_to = \'["password", "cas"]\' WHERE open_to_all')
    with op.batch_alter_table("elections") as batch:
        batch.drop_column("open_to_all")
    with op.batch_alter_table("voters") as batch:
        batch.alter_column("from_registry", new_column_name="self_enrolled")


def downgrade() -> None:
    with op.batch_alter_table("voters") as batch:
        batch.alter_column("self_enrolled", new_column_name="from_registry")
    with op.batch_alter_table("elections") as batch:
        batch.add_column(sa.Column("open_to_all", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Which login methods an election was open to is lost.
    with op.batch_alter_table("elections") as batch:
        batch.drop_column("open_to")
