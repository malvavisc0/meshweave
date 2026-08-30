from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    delete,
    event,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeBase, Mapped, Mapper, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_users_provider_id"),
        Index("ix_users_email", "email"),
        Index("ix_users_provider", "provider", "provider_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="google")
    provider_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationship to crawls (owner). passive_deletes keeps the unit of work
    # from nullifying crawls.user_id itself; the DB-level ON DELETE SET NULL
    # handles public rows and lets the before_delete listener below see the
    # intact user_id when it removes private rows.
    crawls: Mapped[list[Crawl]] = relationship(
        "Crawl", back_populates="user", cascade="save-update", passive_deletes=True
    )


class Prospect(Base):
    __tablename__ = "prospects"
    __table_args__ = (
        UniqueConstraint("user_id", "domain", name="uq_prospects_user_domain"),
        Index("ix_prospects_user_id", "user_id"),
        Index("ix_prospects_domain", "domain"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    crawl_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("crawls.id", ondelete="SET NULL"), nullable=True
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="shortlisted"
    )
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    socials_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ProspectContact(Base):
    __tablename__ = "prospect_contacts"
    __table_args__ = (
        UniqueConstraint("prospect_id", "email", name="uq_prospect_contacts_email"),
        Index("ix_prospect_contacts_prospect_id", "prospect_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    prospect_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    social_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_title: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_expires_at", "expires_at"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_activity: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class OAuthState(Base):
    __tablename__ = "oauth_states"
    __table_args__ = (Index("ix_oauth_states_expires_at", "expires_at"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(255), nullable=False)
    next_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Crawl(Base):
    __tablename__ = "crawls"
    __table_args__ = (
        # Short public key used for URL access (unique across table, nullable for private)
        UniqueConstraint("key", name="uq_crawls_key"),
        Index("ix_crawls_updated_at", "updated_at"),
        Index("ix_crawls_domain", "domain"),
        Index("ix_crawls_user_id", "user_id"),
        Index("ix_crawls_visibility_user_id_listed", "visibility", "user_id", "listed"),
        # Index for history tracking: find latest crawl per domain+visibility
        Index("ix_crawls_domain_is_latest", "domain", "is_latest"),
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

    # Short URL-safe key for public access (/analysis/public/{key}); nullable for private rows
    key: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Visibility and crawl state
    visibility: Mapped[str] = mapped_column(
        String(10), default="public"
    )  # "public" | "private"
    status: Mapped[str] = mapped_column(
        String(10), default="pending"
    )  # "pending" | "running" | "succeeded" | "failed"
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # First-party anonymous browser ID for Langfuse attribution. This is not
    # an account identifier and is superseded by ``user_id`` after sign-in.
    anonymous_user_id: Mapped[str | None] = mapped_column(String(41), nullable=True)
    crawl_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # AEO/GEO scores (computed deterministically from payload_json)
    aeo_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    geo_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    aeo_rating: Mapped[str | None] = mapped_column(String(32), nullable=True)
    geo_rating: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    scoring_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="1.0"
    )
    has_manual_input: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    listed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # History tracking: when True, this is the current row for this domain+visibility
    is_latest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    user: Mapped[User | None] = relationship("User", back_populates="crawls")
    submissions: Mapped[list[Submission]] = relationship(
        "Submission",
        back_populates="crawl",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    score_snapshot: Mapped[ScoreSnapshot | None] = relationship(
        "ScoreSnapshot",
        back_populates="crawl",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


@event.listens_for(User, "before_delete")
def _delete_user_private_crawls(
    mapper: Mapper[User], connection: Connection, target: User
) -> None:
    """Delete the user's private crawls before the user row is deleted.

    ``Crawl.user_id`` uses ON DELETE SET NULL, so without this the user's
    private rows would survive as ownerless private analyses.
    """
    connection.execute(
        delete(Crawl).where(Crawl.user_id == target.id, Crawl.visibility == "private")
    )


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        Index("ix_submissions_created_at", "created_at"),
        Index("ix_submissions_domain", "domain"),
        Index("ix_submissions_client_ip", "client_ip"),
        Index("ix_submissions_session_id", "session_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    crawl_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("crawls.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    url_at_submit: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(10), nullable=False
    )  # "public" | "private"
    force_refresh: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status_at_submit: Mapped[str] = mapped_column(String(10), nullable=False)

    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    forwarded_for: Mapped[str | None] = mapped_column(Text, nullable=True)
    x_real_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    accept_language: Mapped[str | None] = mapped_column(String(255), nullable=True)
    referer: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str | None] = mapped_column(Text, nullable=True)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)

    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    headers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    cookies_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # Relationship back to crawl
    crawl: Mapped[Crawl] = relationship("Crawl", back_populates="submissions")


class ScoreSnapshot(Base):
    """AEO/GEO score snapshot linked to a single crawl.

    Computed deterministically from the crawl payload. Stores composite
    scores, full factor breakdown (``score_json``), and optional
    AI analysis results (``ai_analysis_json``).
    """

    __tablename__ = "score_snapshots"
    __table_args__ = (
        Index("ix_score_snapshots_domain", "domain"),
        Index("ix_score_snapshots_user_id", "user_id"),
        Index("ix_score_snapshots_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    crawl_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("crawls.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)

    # Composite scores (0-100 or NULL)
    aeo_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    geo_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    aeo_rating: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # "Poor"..."Excellent"
    geo_rating: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # "Invisible"..."Dominant"

    # Full score breakdown — JSONB on PostgreSQL
    score_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # AI analysis results — JSONB on PostgreSQL
    ai_analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Scoring metadata
    scoring_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="1.0"
    )
    has_manual_input: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    crawl: Mapped[Crawl] = relationship("Crawl", back_populates="score_snapshot")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_products_user_name"),
        Index("ix_products_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    website: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    contact_info: Mapped[str] = mapped_column(Text, nullable=False)
    defaults_json: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # {"tone":"...","cta":"...","length":"..."}

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
