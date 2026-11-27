"""Unit tests for the timezone-aware cooldown helper."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from webapp.utils.times import cooldown, ensure_utc


def test_cooldown_blocked_never_labels_zero_minutes() -> None:
    """A run blocked by seconds still reports at least one minute left."""
    updated = datetime.now(UTC) - timedelta(seconds=30)
    can_proceed, eta = cooldown(updated, 1)
    assert can_proceed is False
    assert eta == "1m"


def test_cooldown_blocked_rounds_up() -> None:
    updated = datetime.now(UTC) - timedelta(minutes=1)
    can_proceed, eta = cooldown(updated, 5)
    assert can_proceed is False
    assert eta == "4m"


def test_cooldown_elapsed_allows_and_clears_eta() -> None:
    updated = datetime.now(UTC) - timedelta(minutes=30)
    can_proceed, eta = cooldown(updated, 10)
    assert can_proceed is True
    assert eta == ""


def test_cooldown_accepts_naive_db_value() -> None:
    updated = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=30)
    can_proceed, _eta = cooldown(updated, 10)
    assert can_proceed is True


def test_ensure_utc_marks_naive_as_utc() -> None:
    naive = datetime(2026, 1, 1, 12, 0)
    assert ensure_utc(naive).tzinfo is UTC
