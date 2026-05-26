"""Score-to-rating label mapping per spec §4.3."""

AAX_RATINGS: list[tuple[int, int, str]] = [
    (0, 24, "Opaque"),
    (25, 39, "Unclear"),
    (40, 59, "Readable"),
    (60, 79, "Clear"),
    (80, 100, "Fluent"),
]

AEO_RATINGS: list[tuple[int, int, str]] = [
    (0, 25, "Poor"),
    (26, 45, "Below Average"),
    (46, 65, "Average"),
    (66, 85, "Strong"),
    (86, 100, "Excellent"),
]

GEO_RATINGS: list[tuple[int, int, str]] = [
    (0, 25, "Invisible"),
    (26, 45, "Emerging"),
    (46, 65, "Visible"),
    (66, 85, "Authoritative"),
    (86, 100, "Dominant"),
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
