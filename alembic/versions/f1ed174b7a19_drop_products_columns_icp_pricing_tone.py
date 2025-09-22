"""drop products columns icp pricing tone

Revision ID: f1ed174b7a19
Revises: f465b7dd1685
Create Date: 2025-09-17 17:34:28.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1ed174b7a19"
down_revision: Union[str, None] = "f465b7dd1685"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop unused columns from products: icp, pricing, tone
    # Use batch_alter_table for cross-dialect safety
    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_column("icp")
        batch_op.drop_column("pricing")
        batch_op.drop_column("tone")


def downgrade() -> None:
    # Recreate columns as nullable Text to restore previous schema
    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(sa.Column("icp", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("pricing", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("tone", sa.Text(), nullable=True))
