"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = "${up_revision}"
down_revision: Union[str, None] = ${repr(down_revision) if down_revision is not None else "None"}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels) if branch_labels is not None else "None"}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on) if depends_on is not None else "None"}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
