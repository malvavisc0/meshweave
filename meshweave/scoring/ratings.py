"""Score-to-rating label mapping."""

AAX_RATINGS: list[tuple[int, int, str]] = [
    (0, 24, "Opaque"),
    (25, 39, "Unclear"),
    (40, 59, "Readable"),
    (60, 79, "Clear"),
    (80, 100, "Fluent"),
]

AEO_RATINGS: list[tuple[int, int, str]] = [
    (0, 25, "Not extractable"),
    (26, 45, "Limited extractability"),
    (46, 65, "Partially extractable"),
    (66, 85, "Reliably extractable"),
    (86, 100, "Fully extractable"),
]

GEO_RATINGS: list[tuple[int, int, str]] = [
    (0, 25, "Unreachable"),
    (26, 45, "Fragmented"),
    (46, 65, "Reachable"),
    (66, 85, "Connected"),
    (86, 100, "Fully connected"),
]


def aeo_rating(score: float | None) -> str | None:
    """Map AEO score (0-100) to a rating label."""
    if score is None:
        return None
    s = max(0, min(100, int(round(score))))
    for lo, hi, label in AEO_RATINGS:
        if lo <= s <= hi:
            return label
    return "Fully extractable"


def geo_rating(score: float | None) -> str | None:
    """Map GEO score (0-100) to a rating label."""
    if score is None:
        return None
    s = max(0, min(100, int(round(score))))
    for lo, hi, label in GEO_RATINGS:
        if lo <= s <= hi:
            return label
    return "Fully connected"


def aax_rating(score: float | None) -> str | None:
    """Map AAX score (0-100) to a rating label."""
    if score is None:
        return None
    s = max(0, min(100, int(round(score))))
    for lo, hi, label in AAX_RATINGS:
        if lo <= s <= hi:
            return label
    return "Fluent"
