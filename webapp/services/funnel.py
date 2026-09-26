"""Conversion funnel service.

Every write here takes the caller's open session and participates in that
transaction — the event commits or rolls back with the state change that
caused it. Events fire for known users only; anonymous activity is covered
by aggregate Prometheus counters instead.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from webapp.db import get_session
from webapp.models import Crawl, FunnelEvent, FunnelState
from webapp.utils.times import ensure_utc

# Distinct-domain count at which the segment flips to 'agency'.
AGENCY_DOMAIN_THRESHOLD = 5
NUDGE_RESURFACE_DAYS = 7

_STAGE_ORDER = {"registered": 0, "api_consumer": 1, "inquiry": 2, "customer": 3}

EVENT_USER_REGISTERED = "user_registered"
EVENT_ANALYSIS_SAVED = "analysis_saved"
EVENT_ANALYSIS_CLAIMED = "analysis_claimed"
EVENT_ANALYSIS_COMPLETED = "analysis_completed"
EVENT_ANALYSIS_FAILED = "analysis_failed"
EVENT_REPORT_EXPORTED = "report_exported"
EVENT_DIFF_EXPORTED = "diff_exported"
EVENT_CONTACT_MAILTO_CLICKED = "contact_mailto_clicked"
EVENT_RECHECK_COMPLETED = "recheck_completed"
EVENT_GATED_FEATURE_HIT = "gated_feature_hit"
EVENT_GATED_FEATURE_TAKEN = "gated_feature_taken"
EVENT_API_KEY_CREATED = "api_key_created"
EVENT_API_KEY_FIRST_USED = "api_key_first_used"
EVENT_NUDGE_DISMISSED = "nudge_dismissed"
EVENT_SEGMENT_UPGRADED = "segment_upgraded"
EVENT_BECAME_CUSTOMER = "became_customer"


def _now() -> datetime:
    return datetime.now(UTC)


def _state_row(s: Session, user_id: str) -> FunnelState:
    """Return the user's funnel_state row, creating it on first touch."""
    row = s.get(FunnelState, user_id)
    if row is None:
        row = FunnelState(user_id=user_id)
        s.add(row)
        s.flush()
    return row


def _transition_stage(s: Session, state: FunnelState, new_stage: str) -> bool:
    """Move stage forward when new_stage outranks the current one.

    Stages are monotonic: an api_consumer never demotes to registered.
    """
    old_stage = state.stage
    if _STAGE_ORDER.get(new_stage, 0) <= _STAGE_ORDER.get(old_stage, 0):
        return False
    state.stage = new_stage
    try:
        from webapp.utils.metrics import funnel_stage_transitions

        funnel_stage_transitions.labels(old_stage, new_stage).inc()
    except Exception:
        pass
    return True


def _record_actor_domain(s: Session, user_id: str, domain: str | None) -> None:
    """Insert the (user, domain) pair; bump counters only on a fresh insert.

    The INSERT .. ON CONFLICT DO NOTHING is atomic, so concurrent crawl
    completions can neither double-count the domain nor double-emit
    ``segment_upgraded``: the counter bump is a server-side increment, and
    the segment flip is a compare-and-set only one caller can win.
    """
    if not domain:
        return
    result = s.execute(
        text(
            "INSERT INTO funnel_actor_domains (user_id, domain, created_at) "
            "VALUES (:user_id, :domain, :created_at) "
            "ON CONFLICT DO NOTHING"
        ),
        {"user_id": user_id, "domain": domain, "created_at": _now()},
    )
    if not getattr(result, "rowcount", 0):
        return
    _state_row(s, user_id)
    claimed_agency = s.execute(
        text(
            "UPDATE funnel_state SET distinct_domains = distinct_domains + 1 "
            "WHERE user_id = :user_id AND segment != 'agency'"
        ),
        {"user_id": user_id},
    )
    state = _state_row(s, user_id)
    if (
        getattr(claimed_agency, "rowcount", 0)
        and state.distinct_domains >= AGENCY_DOMAIN_THRESHOLD
        and state.segment != "agency"
    ):
        flipped = s.execute(
            text(
                "UPDATE funnel_state SET segment = 'agency' "
                "WHERE user_id = :user_id AND segment != 'agency'"
            ),
            {"user_id": user_id},
        )
        if getattr(flipped, "rowcount", 0):
            state.segment = "agency"
            _insert_event(
                s,
                user_id,
                EVENT_SEGMENT_UPGRADED,
                domain=domain,
                payload={"distinct_domains": state.distinct_domains},
            )


def _insert_event(
    s: Session,
    user_id: str,
    event_type: str,
    *,
    crawl_id: str | None = None,
    domain: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one funnel event row and touch the state row's counters."""
    s.add(
        FunnelEvent(
            id=str(uuid.uuid4()),
            user_id=user_id,
            event_type=event_type,
            crawl_id=crawl_id,
            domain=domain,
            payload=payload,
            created_at=_now(),
        )
    )
    state = _state_row(s, user_id)
    state.last_event_at = _now()
    if event_type == EVENT_ANALYSIS_COMPLETED:
        # Server-side increment: concurrent completions cannot lose counts.
        s.execute(
            text(
                "UPDATE funnel_state SET analyses_count = analyses_count + 1, "
                "last_event_at = :now WHERE user_id = :user_id"
            ),
            {"now": _now(), "user_id": user_id},
        )
    try:
        from webapp.utils.metrics import funnel_events

        funnel_events.labels(event_type).inc()
    except Exception:
        pass


def emit(
    s: Session,
    user_id: str | None,
    event_type: str,
    *,
    crawl_id: str | None = None,
    domain: str | None = None,
    stage: str | None = None,
    **fields: Any,
) -> None:
    """Record a funnel event inside the caller's open transaction.

    No-ops when ``user_id`` is None — anonymous activity never lands in the
    funnel tables. ``fields`` are stored in the small JSONB payload (scope,
    feature, nudge allowlists); ``stage`` requests a stage transition and is
    not stored in the payload.
    """
    if not user_id:
        return
    if event_type == EVENT_ANALYSIS_COMPLETED:
        _record_actor_domain(s, user_id, domain)
    _insert_event(
        s,
        user_id,
        event_type,
        crawl_id=crawl_id,
        domain=domain,
        payload=dict(fields) or None,
    )
    if stage:
        _transition_stage(s, _state_row(s, user_id), stage)


def save_own_analysis(
    s: Session, user_id: str, crawl_id: str, anon_id: str | None
) -> bool:
    """Save an ownerless crawl to the caller's account via a conditional UPDATE.

    Requires the crawl to be ownerless and the browser's anonymous id to
    match the one persisted at submission — proof this browser ran the
    analysis. Race-safe: a concurrent save or claim leaves ``updated == 0``.
    The emit shares the caller's transaction.
    """
    if not anon_id:
        # No cookie means no proof; ``== None`` would otherwise match any
        # ownerless crawl that never got an anon id stamped.
        return False
    updated = (
        s.query(Crawl)
        .filter(
            Crawl.id == crawl_id,
            Crawl.user_id.is_(None),
            Crawl.anonymous_user_id == anon_id,
        )
        .update(
            {"user_id": user_id, "updated_at": _now()},
            synchronize_session=False,
        )
    )
    if updated != 1:
        return False
    row = s.get(Crawl, crawl_id)
    _insert_event(
        s,
        user_id,
        EVENT_ANALYSIS_SAVED,
        crawl_id=crawl_id,
        domain=row.domain if row else None,
    )
    return True


def get_state(user_id: str | None) -> FunnelState | None:
    """Read the user's funnel state row for nudge selection (None when absent)."""
    if not user_id:
        return None
    with get_session() as s:
        row = s.get(FunnelState, user_id)
        if row is None:
            return None
        s.expunge(row)
        return row


def nudge_dismissed(state: FunnelState, nudge: str) -> bool:
    """Whether a nudge is currently suppressed by a recent dismissal."""
    dismissed_at = (state.dismissed_nudges or {}).get(nudge)
    if not dismissed_at:
        return False
    try:
        dismissed_at = datetime.fromisoformat(str(dismissed_at))
    except ValueError:
        return False
    return ensure_utc(dismissed_at) > _now() - timedelta(days=NUDGE_RESURFACE_DAYS)


def record_nudge_dismissal(user_id: str, nudge: str) -> bool:
    """Store the dismissal timestamp (for the resurface window) and emit the event.

    Returns False for an unknown nudge name.
    """
    from webapp.services.nudges import NUDGE_NAMES

    if nudge not in NUDGE_NAMES:
        return False
    with get_session() as s:
        state = _state_row(s, user_id)
        dismissed = dict(state.dismissed_nudges or {})
        dismissed[nudge] = _now().isoformat()
        state.dismissed_nudges = dismissed
        _insert_event(s, user_id, EVENT_NUDGE_DISMISSED, payload={"nudge": nudge})
    return True


def emit_gated_feature_hit(user_id: str | None, *, feature: str, surface: str) -> None:
    """Record that a gated feature was offered to a known user — once.

    Once per user per feature: the first render emits the event and marks
    the gate seen; later renders are silent (the event counts users
    offered to, not impressions). No-ops for anonymous activity.
    """
    if not user_id:
        return
    with get_session() as s:
        state = _state_row(s, user_id)
        if feature in (state.gates_seen or {}):
            return
        seen = dict(state.gates_seen or {})
        seen[feature] = _now().isoformat()
        state.gates_seen = seen
        _insert_event(
            s,
            user_id,
            EVENT_GATED_FEATURE_HIT,
            payload={"feature": feature, "surface": surface},
        )


def record_gate_taken(user_id: str, *, nudge: str) -> None:
    """Record that a user took a gate offer's CTA — once per feature.

    'Taken' is the moment the user acted on an offer (clicked its CTA):
    the strongest pre-conversion signal the gates produce. Distinct
    event name from the offer ('shown') so the funnel never conflates
    rendering with acting.
    """
    with get_session() as s:
        state = _state_row(s, user_id)
        if nudge in (state.gates_taken or {}):
            return
        taken = dict(state.gates_taken or {})
        taken[nudge] = _now().isoformat()
        state.gates_taken = taken
        _insert_event(s, user_id, EVENT_GATED_FEATURE_TAKEN, payload={"feature": nudge})


def mark_customer(
    user_id: str,
    *,
    source: str,
    note: str | None = None,
    contract_value: int | None = None,
) -> bool:
    """Mark a user as a customer (a human's decision, not an automatic one).

    Deal-closed is an offline fact: a real agreement between us and the
    user. No in-product action can prove it, so this is called by an
    operator (CLI) rather than by any request path. ``source`` records
    which journey closed: 'api' (Plan B, paid API) or 'services'
    (Plan A, we did the work). Exactly-once: re-marking a customer is a
    no-op that returns False, so ``became_customer`` never double-emits.

    Args:
        user_id (str): The user's id.
        source (str): 'api' or 'services' — how they became a customer.
        note (Optional[str]): Free-text context (e.g. "retainer, 3 domains").
        contract_value (Optional[int]): Monthly contract value in whole
            currency units, when known. Stored for revenue attribution;
            never shown to the user.

    Returns:
        bool: True when the user transitioned to customer, False when
        they already were one.
    """
    if source not in ("api", "services"):
        raise ValueError("source must be 'api' or 'services'")
    with get_session() as s:
        state = _state_row(s, user_id)
        if state.stage == "customer":
            return False
        _insert_event(
            s,
            user_id,
            EVENT_BECAME_CUSTOMER,
            payload={
                "source": source,
                "note": note,
                "contract_value": contract_value,
            },
        )
        _transition_stage(s, state, "customer")
    return True
