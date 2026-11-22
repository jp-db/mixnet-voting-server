"""voting schedule, elections open to all registered accounts

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-22 21:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("elections") as batch:
        batch.add_column(sa.Column("opens_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("open_to_all", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("voters") as batch:
        batch.add_column(sa.Column("from_registry", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("voters") as batch:
        batch.drop_column("from_registry")
    with op.batch_alter_table("elections") as batch:
        batch.drop_column("open_to_all")
        batch.drop_column("closes_at")
        batch.drop_column("opens_at")
