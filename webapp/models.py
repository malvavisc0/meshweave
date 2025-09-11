import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Crawl(Base):
    __tablename__ = "crawls"
    __table_args__ = (
        # Enforce deduplication at DB-level for public entries
        UniqueConstraint(
            "visibility", "domain", "path", "query", name="uq_crawls_vis_dom_path_query"
        ),
        # Short public key used for URL access (unique across table, nullable for private)
        UniqueConstraint("key", name="uq_crawls_key"),
        Index("ix_crawls_updated_at", "updated_at"),
        Index("ix_crawls_domain", "domain"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Original submitted URL (as-is)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    # Canonicalized components used for deduplication and display
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(
        Text, nullable=False, default="/"
    )  # normalized path (leading '/')
    query: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )  # normalized sorted query string (no leading '?')
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)

    # Short URL-safe key for public access (/k/{key}); nullable for private rows
    key: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Visibility and crawl state
    visibility: Mapped[str] = mapped_column(
        String(10), default="public"
    )  # "public" | "private"
    status: Mapped[str] = mapped_column(
        String(10), default="pending"
    )  # "pending" | "running" | "succeeded" | "failed"
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationship to submissions (enforce delete orphan on Submission when Crawl is deleted)
    submissions: Mapped[list["Submission"]] = relationship(
        "Submission",
        back_populates="crawl",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    crawl_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("crawls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    url_at_submit: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(10), nullable=False
    )  # "public" | "private"
    force_refresh: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status_at_submit: Mapped[str] = mapped_column(String(10), nullable=False)

    client_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    client_ip_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    forwarded_for: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    x_real_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    accept_language: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    referer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    origin: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    host: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    headers_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cookies_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationship back to crawl
    crawl: Mapped["Crawl"] = relationship("Crawl", back_populates="submissions")

    __table_args__ = (
        Index("ix_submissions_created_at", "created_at"),
        Index("ix_submissions_domain", "domain"),
        Index("ix_submissions_client_ip", "client_ip"),
        Index("ix_submissions_session_id", "session_id"),
    )
