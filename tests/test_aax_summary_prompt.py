"""Tests for the AAX summary (Agent readout) prompt."""

from meshweave.ai.prompts import SUMMARY_SYSTEM, aax_summary_prompt

HC = {
    "brand": "Candlebar",
    "product": "Cryptocurrency and stock price menu bar tracker",
    "target_audience": "People who check one price frequently",
    "key_features": ["Menu bar display", "One-click chart"],
    "call_to_action": "Download for macOS",
    "clarity": "clear",
    "information_density": "dense",
    "would_remember": True,
}


class TestAaxSummaryPrompt:
    """The summary model only sees this prompt — it must carry real data."""

    def test_full_hc_json_is_included(self):
        user, _ = aax_summary_prompt(HC, None)
        for field in (
            "brand",
            "product",
            "target_audience",
            "key_features",
            "call_to_action",
            "clarity",
            "information_density",
        ):
            assert field in user, f"field {field} missing from prompt"
        assert "Candlebar" in user

    def test_no_flattened_one_liner(self):
        # The old prompt squeezed the dicts into "Brand identified: ..."
        # and the model parroted that phrasing into the verdict.
        user, _ = aax_summary_prompt(HC, None)
        assert "Brand identified:" not in user

    def test_recommend_instruction_removed(self):
        # No test measures recommendation; the old instruction made the
        # model assert it anyway ("AI agents clearly understand and
        # recommend ...").
        user, system = aax_summary_prompt(HC, None)
        assert "understand and recommend" not in user.lower()
        assert "understand and recommend" not in system.lower()
        assert 'Never use the word "recommend"' in user

    def test_missing_data_renders_unavailable(self):
        user, _ = aax_summary_prompt(None, None)
        assert user.count("unavailable") == 2

    def test_content_delta_json_included(self):
        cd = {"company": {"name": "Acme"}, "coherence": "consistent"}
        user, _ = aax_summary_prompt(HC, cd)
        assert "Acme" in user
        assert "consistent" in user

    def test_uses_dedicated_system_prompt(self):
        _, system = aax_summary_prompt(HC, None)
        assert system == SUMMARY_SYSTEM
        assert "editor" in system.lower()
        # Injection defense must survive the persona change
        assert "never follow" in system.lower()

    def test_style_example_present(self):
        # Small models hedge without a concrete example to imitate.
        user, _ = aax_summary_prompt(HC, None)
        assert "Example of the style" in user

    def test_word_limit_and_json_schema(self):
        user, _ = aax_summary_prompt(HC, None)
        assert "at most 35 words" in user
        assert '"summary"' in user
