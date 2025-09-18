"""drop products unused cols (icp, pricing, tone)

Revision ID: b3a1c2d4e5f6
Revises: f1ed174b7a19
Create Date: 2025-09-17 17:31:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3a1c2d4e5f6"
down_revision: Union[str, None] = "f1ed174b7a19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: columns were dropped in revision f1ed174b7a19.
    # This revision exists only to fix the migration chain and avoid branching heads.
    pass


def downgrade() -> None:
    # No-op: downgrading to f1ed174b7a19 should not re-add columns here.
    pass
