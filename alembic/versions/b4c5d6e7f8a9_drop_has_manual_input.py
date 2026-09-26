"""drop has_manual_input columns

Revision ID: b4c5d6e7f8a9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the manual-input flags: the manual external factors are gone."""
    with op.batch_alter_table("crawls") as batch_op:
        batch_op.drop_column("has_manual_input")
    with op.batch_alter_table("score_snapshots") as batch_op:
        batch_op.drop_column("has_manual_input")


def downgrade() -> None:
    """Restore the flags (no data recovery — defaults to False)."""
    with op.batch_alter_table("score_snapshots") as batch_op:
        batch_op.add_column(
            sa.Column(
                "has_manual_input",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    with op.batch_alter_table("crawls") as batch_op:
        batch_op.add_column(
            sa.Column(
                "has_manual_input",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
