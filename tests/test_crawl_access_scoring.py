"""Tests for GEO crawl-access bot status scoring.

A bot that is allowed site-wide except specific paths (e.g. private API
endpoints) is reported as ``partially_restricted`` by the robots parser and
must earn partial credit — not zero.
"""

from meshweave.scoring.geo import score_crawl_access


def _payload(bots: dict[str, str]) -> dict:
    return {
        "robots": {
            "exists": True,
            "bots": bots,
            "sitemaps": ["http://x/sitemap.xml"],
        },
        "llms_txt": {
            "llms_txt": {"exists": True},
            "llms_full_txt": {"exists": True},
        },
    }


def test_fully_allowed_bots_earn_full_points():
    raw = score_crawl_access(
        _payload(
            {"GPTBot": "allowed", "ClaudeBot": "allowed", "PerplexityBot": "allowed"}
        )
    )
    # 8 robots + 15 + 12 + 12 bots + 15 llms + 8 llms-full + 7 sitemap
    assert raw["score"] == 77.0


def test_partially_restricted_bots_earn_half_points():
    raw = score_crawl_access(
        _payload(
            {
                "GPTBot": "partially_restricted",
                "ClaudeBot": "partially_restricted",
                "PerplexityBot": "partially_restricted",
            }
        )
    )
    # 8 robots + 7 + 6 + 6 bots + 15 llms + 8 llms-full + 7 sitemap
    assert raw["score"] == 57.0


def test_blocked_bots_earn_nothing():
    raw = score_crawl_access(
        _payload(
            {"GPTBot": "blocked", "ClaudeBot": "blocked", "PerplexityBot": "blocked"}
        )
    )
    # 8 robots + 15 llms + 8 llms-full + 7 sitemap only
    assert raw["score"] == 38.0
