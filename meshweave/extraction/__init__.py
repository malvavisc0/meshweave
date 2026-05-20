"""Page content extraction: links, emails, metadata, markdown, HTML cleaning."""

from .audit import (
    audit_entity_consistency,
    audit_meta_uniqueness,
    audit_schema_coverage,
)
from .clean import preprocess_soup
from .emails import collect_emails, deduplicate_sources, extract_emails, is_valid_email
from .links import classify_links
from .markdown import soup_from_html, to_markdown
from .meta import extract_page_meta
from .robots import check_llms_txt, fetch_robots_info
from .structure import analyze_faq_schema, extract_content_metrics, extract_headings

__all__ = [
    "analyze_faq_schema",
    "audit_entity_consistency",
    "audit_meta_uniqueness",
    "audit_schema_coverage",
    "check_llms_txt",
    "classify_links",
    "collect_emails",
    "deduplicate_sources",
    "extract_content_metrics",
    "extract_emails",
    "extract_headings",
    "extract_page_meta",
    "fetch_robots_info",
    "is_valid_email",
    "preprocess_soup",
    "soup_from_html",
    "to_markdown",
]
