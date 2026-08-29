"""Content structure analysis for AEO/GEO scoring signals."""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup
from bs4.element import Tag

__all__ = [
    "extract_headings",
    "extract_content_metrics",
    "analyze_faq_schema",
]


_MAX_HEADING_LEN = 200


def extract_headings(soup: BeautifulSoup) -> dict[str, Any]:
    """Extract heading hierarchy from a parsed HTML page.

    Returns a dict with h1-h6 lists, depth, and h1 count.
    Headings longer than ``_MAX_HEADING_LEN`` characters are
    truncated with an ellipsis to avoid ingesting mismarked
    body content (e.g. legal pages wrapping paragraphs in
    ``<h2>`` tags).
    """
    headings: dict[str, list[str]] = {f"h{i}": [] for i in range(1, 7)}
    max_depth = 0

    for level in range(1, 7):
        tag_name = f"h{level}"
        for tag in soup.find_all(tag_name):
            if isinstance(tag, Tag):
                text = tag.get_text(strip=True)
                if text:
                    if len(text) > _MAX_HEADING_LEN:
                        text = text[:_MAX_HEADING_LEN] + "…"
                    headings[tag_name].append(text)
                    max_depth = max(max_depth, level)

    return {
        **headings,
        "depth": max_depth,
        "h1_count": len(headings["h1"]),
        "total": sum(len(v) for v in headings.values()),
    }


def extract_content_metrics(
    soup: BeautifulSoup,
    markdown: str = "",
) -> dict[str, Any]:
    """Extract content depth metrics from HTML and/or markdown.

    Counts paragraphs, lists, tables, code blocks, and images
    (with alt text) to gauge content richness.
    """
    body = soup.find("body") or soup

    paragraphs = len(body.find_all("p"))
    lists = len(body.find_all(("ul", "ol")))
    tables = len(body.find_all("table"))
    code_blocks = len(body.find_all(("pre", "code")))

    # If no <p> tags found but markdown has content, estimate
    # paragraphs from double-newline-separated blocks.
    if paragraphs == 0 and markdown:
        blocks = [
            b.strip()
            for b in markdown.split("\n\n")
            if b.strip() and not b.strip().startswith(("#", "|", "```"))
        ]
        paragraphs = len(blocks)

    # Count meaningful images, skipping decorative ones
    img_total, img_with_alt = _count_images(body)

    # Word count from markdown if available, else body text
    if markdown:
        words = len(markdown.split())
    else:
        text = body.get_text(separator=" ", strip=True)
        words = len(text.split())

    heading_count = sum(len(body.find_all(f"h{i}")) for i in range(1, 7))

    return {
        "words": words,
        "paragraphs": paragraphs,
        "lists": lists,
        "tables": tables,
        "code_blocks": code_blocks,
        "images_total": img_total,
        "images_with_alt": img_with_alt,
        "headings": heading_count,
    }


def _count_images(soup: Any) -> tuple[int, int]:
    """Count meaningful images (non-decorative, non-pixel) and those with alt."""
    img_total = 0
    img_with_alt = 0
    for img in soup.find_all("img"):
        if not isinstance(img, Tag):
            continue
        if not _is_meaningful_image(img):
            continue
        img_total += 1
        if str(img.get("alt", "")).strip():
            img_with_alt += 1
    return img_total, img_with_alt


def _is_meaningful_image(img: Tag) -> bool:
    """True when an image is content, not decorative or a tracking pixel."""
    if img.get("role") == "presentation":
        return False
    if str(img.get("aria-hidden", "")).lower() == "true":
        return False
    src = str(img.get("src", ""))
    # Skip tracking pixels and data-URI placeholders
    if not src or src.startswith("data:"):
        return False
    # Skip 1x1 tracking pixels by dimension
    return not _is_tracking_pixel(img)


def _is_tracking_pixel(img: Tag) -> bool:
    """True when width/height attributes are present and <= 1 pixel."""
    w = str(img.get("width", ""))
    h = str(img.get("height", ""))
    try:
        return int(w) <= 1 or int(h) <= 1
    except ValueError:
        return False
    except TypeError:
        return False


_OPTIMAL_MIN = 40
_OPTIMAL_MAX = 60


def analyze_faq_schema(
    jsonld_items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Analyse FAQ schema entries for AEO answer quality.

    Returns None if no FAQPage schema is present.
    Checks answer word counts against the 40-60 word optimal range.
    """
    all_questions = _faq_questions(jsonld_items)
    if not all_questions:
        return None

    word_counts = [q["answer_words"] for q in all_questions]
    return _faq_summary(all_questions, word_counts)


def _faq_questions(jsonld_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Question detail dicts from every FAQPage schema entry."""
    questions: list[dict[str, Any]] = []
    for item in jsonld_items:
        if item.get("@type") != "FAQPage":
            continue
        for q in item.get("mainEntity", []):
            if q.get("@type") != "Question":
                continue
            questions.append(_faq_question(q))
    return questions


def _faq_question(q: dict[str, Any]) -> dict[str, Any]:
    """Detail dict for one FAQ Question entry."""
    answer = q.get("acceptedAnswer", {})
    text = answer.get("text", "")
    word_count = len(text.split())
    return {
        "question": q.get("name", ""),
        "answer_words": word_count,
        "in_optimal_range": _OPTIMAL_MIN <= word_count <= _OPTIMAL_MAX,
    }


def _faq_summary(
    all_questions: list[dict[str, Any]],
    word_counts: list[int],
) -> dict[str, Any]:
    """Aggregate FAQ answer-quality summary across all questions."""
    in_range = sum(1 for q in all_questions if q["in_optimal_range"])
    too_short = sum(1 for w in word_counts if w < _OPTIMAL_MIN)
    too_long = sum(1 for w in word_counts if w > _OPTIMAL_MAX)

    return {
        "count": len(all_questions),
        "avg_answer_words": round(sum(word_counts) / len(word_counts), 1),
        "answers_in_optimal_range": in_range,
        "answers_too_short": too_short,
        "answers_too_long": too_long,
        "optimal_range": f"{_OPTIMAL_MIN}-{_OPTIMAL_MAX} words",
        "details": all_questions,
    }
