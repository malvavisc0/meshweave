"""Visual CTA audit persistence.

Split from :mod:`webapp.models` so the over-cap models module does not grow;
the model registers on the shared ``Base``, so alembic autogenerate and
``Base.metadata.create_all`` see it whenever this module is imported.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from webapp.models import Base


class VisualAudit(Base):
    """One stored visual CTA assessment per crawl; a re-run replaces it.

    ``claimed_at`` marks an owner capture in flight; a claim row for a
    never-captured crawl carries a NULL ``screenshot_png``. Claims never
    touch stored data, so a failed re-run leaves the last successful
    capture intact.
    """

    __tablename__ = "visual_audits"
    __table_args__ = (Index("ix_visual_audits_created_at", "created_at"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    crawl_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("crawls.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    assessment_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    attention_model: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    viewport: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    element_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    screenshot_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    overlay_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    capture_version: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
