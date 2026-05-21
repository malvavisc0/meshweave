"""Score-to-rating label mapping per spec §4.3."""

AAX_RATINGS: list[tuple[int, int, str]] = [
    (0, 20, "Opaque"),
    (21, 40, "Unclear"),
    (41, 60, "Readable"),
    (61, 80, "Clear"),
    (81, 100, "Fluent"),
]

AEO_RATINGS: list[tuple[int, int, str]] = [
    (0, 20, "Poor"),
    (21, 40, "Below Average"),
    (41, 60, "Average"),
    (61, 80, "Strong"),
    (81, 100, "Excellent"),
]

GEO_RATINGS: list[tuple[int, int, str]] = [
    (0, 20, "Invisible"),
    (21, 40, "Emerging"),
    (41, 60, "Visible"),
    (61, 80, "Authoritative"),
    (81, 100, "Dominant"),
]


def aeo_rating(score: float | None) -> str | None:
    """Map AEO score (0-100) to a rating label."""
    if score is None:
        return None
    s = max(0, min(100, int(round(score))))
    for lo, hi, label in AEO_RATINGS:
        if lo <= s <= hi:
            return label
    return "Excellent"


def geo_rating(score: float | None) -> str | None:
    """Map GEO score (0-100) to a rating label."""
    if score is None:
        return None
    s = max(0, min(100, int(round(score))))
    for lo, hi, label in GEO_RATINGS:
        if lo <= s <= hi:
            return label
    return "Dominant"


def aax_rating(score: float | None) -> str | None:
    """Map AAX score (0-100) to a rating label."""
    if score is None:
        return None
    s = max(0, min(100, int(round(score))))
    for lo, hi, label in AAX_RATINGS:
        if lo <= s <= hi:
            return label
    return "Fluent"
