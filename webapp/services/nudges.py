"""Nudge selection for the funnel partial.

Priority order per the plan: stage-1 CTA > gate offer > first-call hint,
at most one nudge per page, dismissible with a ~7-day "not now" resurface.

Selection is viewer-aware: the caller passes the viewer role and whether
the viewer already owns an active API key, so the result page never shows
"save this analysis" to anonymous visitors or to the analysis's owner, and
the first-call hint disappears once the key has actually been used.
"""

from __future__ import annotations

from typing import Any

from webapp.db import get_session
from webapp.models import ApiKey
from webapp.services import funnel as funnel_svc

# Registered nudge names (the dismissal allowlist).
NUDGE_NAMES = frozenset(
    {"save_analysis", "bulk_gate", "first_call_hint", "services_offer"}
)

# Analyses a user must have completed before the Plan A "we do it for you"
# offer appears — enough work shown that remediation is a real cost.
SERVICES_OFFER_MIN_ANALYSES = 3

# Surfaces a nudge may render on.
SURFACES = ("result", "dashboard", "profile")


def has_active_api_key(user_id: str | None) -> bool:
    """Whether the user owns at least one non-revoked API key."""
    if not user_id:
        return False
    try:
        with get_session() as s:
            row = (
                s.query(ApiKey.id)
                .filter(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
                .first()
            )
            return row is not None
    except Exception:
        return False


def key_first_used(user_id: str | None) -> bool:
    """Whether any of the user's API keys has been used at least once."""
    if not user_id:
        return False
    try:
        with get_session() as s:
            row = (
                s.query(ApiKey.id)
                .filter(
                    ApiKey.user_id == user_id,
                    ApiKey.first_used_at.isnot(None),
                )
                .first()
            )
            return row is not None
    except Exception:
        return False


def _first_call_example() -> str:
    """A copy-paste first call for the first_call_hint nudge."""
    return (
        'curl -H "Authorization: Bearer mw_live_YOURKEY" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        '  -d \'{"urls": ["https://example.com"], "scope": "page"}\' \\\n'
        "  https://meshweaveai.com/api/v1/analyses"
    )


def _record_impression(
    user_id: str, surface: str, state: Any, nudge: dict[str, Any]
) -> None:
    """Bump the impression counter and record gated-feature hits."""
    try:
        from webapp.utils.metrics import funnel_nudge_shown

        funnel_nudge_shown.labels(
            surface,
            getattr(state, "stage", None) or "registered",
            getattr(state, "segment", None) or "standard",
        ).inc()
    except Exception:
        pass
    # A gate offer shown to a user without a key is a gated-feature
    # hit: the funnel needs to know how often the gate does its job.
    # A gate offer shown to a user without a key is a gated-feature
    # hit; the services offer is Plan A — it is tracked via its CTA.
    if nudge.get("name") in ("bulk_gate", "save_analysis") and not has_active_api_key(
        user_id
    ):
        try:
            funnel_svc.emit_gated_feature_hit(
                user_id, feature=nudge["name"], surface=surface
            )
        except Exception:
            pass


def funnel_context(
    user_id: str | None,
    surface: str,
    viewer_role: str | None = None,
) -> dict[str, Any]:
    """Nudge selection + impression metric for one page render.

    One shared implementation for every surface: selects the nudge for the
    user's funnel state and viewer role, bumps the impression counter when
    a nudge renders, and never renders for anonymous users.
    """
    if not user_id:
        return {"funnel_nudge": None}
    try:
        state = funnel_svc.get_state(user_id)
        first_used = key_first_used(user_id)
        has_key = has_active_api_key(user_id)
        nudge = select_nudge(surface, state, viewer_role, first_used, has_key)
    except Exception:
        return {"funnel_nudge": None}
    if nudge is not None:
        _record_impression(user_id, surface, state, nudge)
    return {"funnel_nudge": nudge}


def _base_nudges(
    surface: str, segment: str, viewer_role: str | None, analyses_count: int = 0
) -> list[dict[str, Any]]:
    """The candidate nudges for a surface, highest priority first.

    ``viewer_role`` gates the result-surface candidates: only a signed-in
    non-owner who could actually save the analysis sees save_analysis.
    Users with several completed analyses but no API conversion are the
    Plan A audience: they have work to do and no personnel to do it, so
    the services offer outranks the gate.
    """
    candidates: list[dict[str, Any]] = []
    if surface == "result" and viewer_role == "public_non_owner":
        candidates.append(
            {
                "name": "save_analysis",
                "title": "Save this analysis to your account",
                "body": "Keep this report and its score history in your dashboard.",
                "cta": {"label": "Save to my account", "href": "/dashboard"},
            }
        )
    # The first-call hint outranks the gate offers: a key holder that has
    # not called yet needs the nudge over the pitch.
    candidates.append(
        {
            "name": "first_call_hint",
            "title": "Your API key is ready",
            "body": "Make your first call — here is a copy-paste example.",
            "cta": {"label": "API docs", "href": "/.well-known/llms-full.txt"},
            "example": _first_call_example(),
        }
    )
    if analyses_count >= SERVICES_OFFER_MIN_ANALYSES:
        candidates.append(
            {
                "name": "services_offer",
                "title": "Want the fixes done for you?",
                "body": (
                    f"You have run {analyses_count} analyses. If making these changes in-house "
                    "is a squeeze, our team can do the remediation for you."
                ),
                "cta": {"label": "Talk to us", "href": "/contact"},
            }
        )
    if surface in ("result", "dashboard"):
        candidates.append(
            {
                "name": "bulk_gate",
                "title": (
                    "Audit your whole prospect list, not one site at a time"
                    if segment == "agency"
                    else "Audit multiple domains at once"
                ),
                "body": "Bulk audits run with a free API key — one click creates it.",
                "cta": {
                    "label": "Create a free API key",
                    "href": "/profile#api-access",
                },
            }
        )
    return candidates


def _eligible(
    nudge: dict[str, Any], stage: str, first_used: bool, has_key: bool
) -> bool:
    """Whether a candidate nudge applies to this user right now."""
    if nudge["name"] == "first_call_hint":
        # Only meaningful for a key holder that has not made a call yet.
        return has_key and not first_used
    if nudge["name"] == "bulk_gate":
        # The gate pitch targets users that have not crossed it yet.
        return not has_key
    # Save/services offers target users that have not yet shown contact
    # intent (inquiry+) or converted (customer).
    return stage in ("registered", "api_consumer")


def select_nudge(
    surface: str,
    state: Any,
    viewer_role: str | None = None,
    first_used: bool = False,
    has_key: bool = False,
) -> dict | None:
    """Return the highest-priority non-dismissed nudge for the surface.

    ``state`` is the user's FunnelState row (or None). Anonymous viewers
    get no nudges at all — the result page's stage-1 CTA covers them.
    Key facts (``first_used``/``has_key``) gate the API-flavored nudges:
    the hint only for a key holder without calls, the gate pitch only for
    users without a key.
    """
    if surface not in SURFACES or state is None:
        return None
    segment = getattr(state, "segment", None) or "standard"
    stage = getattr(state, "stage", None) or "registered"
    analyses_count = int(getattr(state, "analyses_count", 0) or 0)
    for nudge in _base_nudges(surface, segment, viewer_role, analyses_count):
        if not _eligible(nudge, stage, first_used, has_key):
            continue
        if funnel_svc.nudge_dismissed(state, nudge["name"]):
            continue
        return nudge
    return None
