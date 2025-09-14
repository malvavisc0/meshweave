import uuid
from datetime import datetime, timezone
from typing import List, Optional

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


# New: Users table (Phase 1 schema)
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
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="google")
    provider_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

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

    # Relationship to crawls (owner)
    crawls: Mapped[List["Crawl"]] = relationship(
        "Crawl", back_populates="user", cascade="save-update"
    )


# New: Auth sessions table (Phase 1 schema)
class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_session_id", "session_id"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

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
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class OAuthState(Base):
    __tablename__ = "oauth_states"
    __table_args__ = (Index("ix_oauth_states_expires_at", "expires_at"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    sid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(255), nullable=False)
    next_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
        Index("ix_crawls_user_id", "user_id"),
        Index("ix_crawls_scope", "scope"),
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

    # Phase 1 schema additions for ownership and site crawling
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    scope: Mapped[str] = mapped_column(String(10), default="page")  # "page" | "site"
    limits_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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

    # Relationships
    user: Mapped[Optional["User"]] = relationship("User", back_populates="crawls")
    submissions: Mapped[list["Submission"]] = relationship(
        "Submission",
        back_populates="crawl",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    links: Mapped[list["CrawlLink"]] = relationship(
        "CrawlLink",
        back_populates="crawl",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    emails: Mapped[list["CrawlEmail"]] = relationship(
        "CrawlEmail",
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


class CrawlLink(Base):
    __tablename__ = "crawl_links"
    __table_args__ = (
        UniqueConstraint(
            "crawl_id", "page_url", "absolute_url", "type", name="uq_crawl_links_unique"
        ),
        Index("ix_crawl_links_crawl_id", "crawl_id"),
        Index("ix_crawl_links_domain", "domain"),
        Index("ix_crawl_links_type", "type"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    crawl_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("crawls.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_url: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    absolute_url: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(
        String(10), nullable=False
    )  # "internal" | "external"
    domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationship back to crawl
    crawl: Mapped["Crawl"] = relationship("Crawl", back_populates="links")


class CrawlEmail(Base):
    __tablename__ = "crawl_emails"
    __table_args__ = (
        UniqueConstraint("crawl_id", "page_url", "email", name="uq_crawl_emails_unique"),
        Index("ix_crawl_emails_crawl_id", "crawl_id"),
        Index("ix_crawl_emails_email", "email"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    crawl_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("crawls.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_url: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)  # lowercased
    found_as: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )  # e.g. "mailto,text"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationship back to crawl
    crawl: Mapped["Crawl"] = relationship("Crawl", back_populates="emails")
