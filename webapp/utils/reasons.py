"""Friendly reason stopped mappings for crawl summaries."""

import re

# Mapping of internal stop reasons to user-friendly labels
REASON_MAPPINGS: dict[str, str] = {
    "time_budget_exhausted": "Time budget reached",
    "max_pages_reached": "Max pages reached",
    "max_depth_reached": "Max depth reached",
    "robots_disallowed": "Blocked by robots.txt",
    "network_timeout": "Network timeout",
    "http_error": "HTTP error",
    "render_error": "Rendering failed",
    "crawl_completed": "Completed",
    "duplicate_detected": "Duplicate URL",
    "user_cancelled": "Cancelled by user",
    "rate_limited": "Rate limited",
    "server_error": "Server error; please try again",
}


def friendly_reason(code: str) -> str:
    """Convert internal stop reason code to user-friendly label.

    Falls back to title-cased transformation for unknown codes.

    Args:
        code: Internal reason code (e.g., 'time_budget_exhausted').

    Returns:
        Friendly label string.
    """
    if not code:
        return "N/A"
    friendly = REASON_MAPPINGS.get(code.strip().lower())
    if friendly:
        return friendly
    # Fallback: title-case unknown codes, replace underscores with spaces
    return re.sub(r"_+", " ", code.strip()).title()


# Public-safe labels for crawl error codes shown to non-owner viewers.
PUBLIC_ERROR_LABELS: dict[str, str] = {
    "time_budget_exceeded": "Time budget reached",
    "cancelled_by_user": "Cancelled by the owner",
}


def public_error_label(error: str | None) -> str:
    """Return a public-safe label for a stored crawl error string.

    Known error codes map to fixed labels; anything else (including raw
    exception text) collapses to a generic label so internal details are
    never surfaced to non-owners.

    Args:
        error: Stored crawl error string.

    Returns:
        Public-safe label string, or '' when there is no error.
    """
    if not error:
        return ""
    return PUBLIC_ERROR_LABELS.get(error.strip().lower(), "Analysis failed")
