"""make products.website and contact_info non-nullable

Revision ID: c660a02af043
Revises: b3a1c2d4e5f6
Create Date: 2025-09-22 07:42:42.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c660a02af043"
down_revision: Union[str, None] = "b3a1c2d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Set NOT NULL constraints on products.website and products.contact_info
    with op.batch_alter_table("products") as batch_op:
        batch_op.alter_column(
            "website",
            existing_type=sa.Text(),
            nullable=False,
        )
        batch_op.alter_column(
            "contact_info",
            existing_type=sa.Text(),
            nullable=False,
        )


def downgrade() -> None:
    # Revert NOT NULL constraints back to nullable
    with op.batch_alter_table("products") as batch_op:
        batch_op.alter_column(
            "website",
            existing_type=sa.Text(),
            nullable=True,
        )
        batch_op.alter_column(
            "contact_info",
            existing_type=sa.Text(),
            nullable=True,
        )
