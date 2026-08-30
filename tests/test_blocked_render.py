"""Tests for bot-protection interstitial detection."""

from meshweave.crawling.blocked import blocked_render_reason


def _payload(status: int = 200, title: str = "Acme — Widgets", markdowns=None) -> dict:
    return {
        "metrics": {"render": {"response_status": status}},
        "page": {"title": title},
        "markdowns": markdowns if markdowns is not None else {},
    }


_THIN = {"markdown": "blocked x"}  # far below 50 words
_REAL = {"markdown": "word " * 60}  # substantive content


class TestBlockedRenderReason:
    """Detect blocked renders from status and interstitial titles."""

    def test_vercel_checkpoint_429(self):
        # Regression: rephraseai.com served a Vercel Security Checkpoint
        # with HTTP 429 and was still scored as a successful crawl.
        p = _payload(status=429, title="Vercel Security Checkpoint")
        reason = blocked_render_reason(p)
        assert reason is not None
        assert "429" in reason
        assert "Vercel Security Checkpoint" in reason

    def test_vercel_checkpoint_200(self):
        # Challenge flows sometimes serve HTTP 200 — the title must
        # still trip detection.
        p = _payload(status=200, title="Vercel Security Checkpoint")
        reason = blocked_render_reason(p)
        assert reason is not None
        assert "interstitial" in reason

    def test_cloudflare_just_a_moment(self):
        p = _payload(status=403, title="Just a moment...")
        assert blocked_render_reason(p) is not None

    def test_challenge_title_with_thin_content(self):
        # HTTP 200 + challenge title + no substantive markdown → block.
        p = _payload(
            status=200,
            title="Just a moment...",
            markdowns={"https://x.com/": _THIN},
        )
        reason = blocked_render_reason(p)
        assert reason is not None
        assert "interstitial" in reason

    def test_vercel_checkpoint_200_thin_content(self):
        # Challenge flows sometimes serve HTTP 200 — the title plus thin
        # content must still trip detection.
        p = _payload(status=200, title="Vercel Security Checkpoint")
        reason = blocked_render_reason(p)
        assert reason is not None
        assert "interstitial" in reason

    def test_captcha_service_site_not_blocked(self):
        # False-positive guard: a captcha-solving service whose title
        # contains "captcha" but whose pages carry real content must
        # NOT be flagged as an interstitial.
        p = _payload(
            status=200,
            title="2Captcha — Image Verification Service",
            markdowns={"https://2captcha.com/": _REAL},
        )
        assert blocked_render_reason(p) is None

    def test_challenge_title_with_real_content_not_blocked(self):
        # Partial block: start page title matches a challenge fragment
        # but sub-pages yielded real content — not a full block.
        p = _payload(
            status=200,
            title="Attention Required!",
            markdowns={"https://x.com/blog": _REAL},
        )
        assert blocked_render_reason(p) is None

    def test_refusal_status_without_title(self):
        p = _payload(status=403, title="")
        reason = blocked_render_reason(p)
        assert reason is not None
        assert "403" in reason

    def test_normal_site_not_blocked(self):
        assert (
            blocked_render_reason(_payload(status=200, title="Acme — Widgets")) is None
        )

    def test_redirect_status_not_blocked(self):
        # 301/302/304 are not refusals; the renderer follows redirects.
        assert blocked_render_reason(_payload(status=200, title="Acme")) is None

    def test_404_not_interstitial(self):
        # A 404 is a missing page, not bot protection — and the crawler
        # surfaces it through content, not this check.
        p = _payload(status=404, title="Not Found")
        # 404 is not in the blocked set; title has no fragments.
        assert blocked_render_reason(p) is None

    def test_title_fragment_requires_thin_content(self):
        # "Captcha Solver for Developers" with thin content IS a
        # challenge-shaped page; with real content it is a product
        # page (see test_captcha_service_site_not_blocked).
        p = _payload(status=200, title="Captcha Solver for Developers")
        assert blocked_render_reason(p) is not None
        p_real = _payload(
            status=200,
            title="Captcha Solver for Developers",
            markdowns={"https://x.com/": _REAL},
        )
        assert blocked_render_reason(p_real) is None

    def test_missing_metrics_and_page(self):
        assert blocked_render_reason({}) is None

    def test_non_dict_payload(self):
        assert blocked_render_reason(None) is None  # type: ignore[arg-type]

    def test_malformed_status(self):
        p = {
            "metrics": {"render": {"response_status": "garbage"}},
            "page": {"title": "Acme"},
        }
        assert blocked_render_reason(p) is None
