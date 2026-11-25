"""High-level visual audit wiring with the browser and model stubbed."""

from __future__ import annotations

import pytest

from meshweave import visual_audit
from meshweave.crawling.visual import VisualElement, VisualSnapshot
from meshweave.scoring.saliency import SALIENCY_MODEL_ENV


def _element(**overrides: object) -> VisualElement:
    base = VisualElement(
        tag="button",
        role="",
        name="Start a campaign",
        text="Start a campaign",
        href="",
        input_type="",
        x=40,
        y=120,
        width=180,
        height=48,
        above_fold=True,
        in_viewport=True,
        disabled=False,
        focusable=True,
        has_accessible_name=True,
        occluded=False,
        has_own_background=True,
        background_color="rgb(11, 95, 255)",
        background_estimated=False,
        surrounding_color="rgb(255, 255, 255)",
        styles={
            "color": "rgb(255, 255, 255)",
            "font_size": "16px",
            "font_weight": "600",
        },
    )
    return VisualElement(**{**base.__dict__, **overrides})


def _snapshot() -> VisualSnapshot:
    return VisualSnapshot(
        url="https://voxlynow.com/",
        final_url="https://www.voxlynow.com/",
        viewport_width=1366,
        viewport_height=768,
        scroll_height=2400,
        screenshot_png=b"png",
        elements=(_element(),),
    )


@pytest.mark.asyncio
async def test_audit_without_model_skips_attention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_capture(url: str, **kwargs: object) -> VisualSnapshot:
        return _snapshot()

    monkeypatch.setattr(visual_audit, "capture_visual_snapshot", fake_capture)
    monkeypatch.delenv(SALIENCY_MODEL_ENV, raising=False)

    audit = await visual_audit.run_visual_audit(
        "https://voxlynow.com/", cta_text="Start a campaign"
    )

    assert audit.attention is None
    assert audit.assessment.element is not None
    assert audit.assessment.element.name == "Start a campaign"
    payload = audit.to_dict()
    assert payload["attention_model"] is False
    assert payload["url"] == "https://www.voxlynow.com/"
    assert payload["assessment"]["element"]["name"] == "Start a campaign"


@pytest.mark.asyncio
async def test_audit_uses_model_path_for_attention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMap:
        def share(self, **kwargs: int) -> float:
            return 0.05

    class FakeModel:
        def __init__(self, path: str) -> None:
            assert path == "/models/fastsal.onnx"

        def predict(self, png: bytes) -> FakeMap:
            assert png == b"png"
            return FakeMap()

    async def fake_capture(url: str, **kwargs: object) -> VisualSnapshot:
        return _snapshot()

    monkeypatch.setattr(visual_audit, "capture_visual_snapshot", fake_capture)
    monkeypatch.setattr(visual_audit, "load_saliency_model", FakeModel)

    audit = await visual_audit.run_visual_audit(
        "https://voxlynow.com/",
        saliency_model_path="/models/fastsal.onnx",
    )

    assert audit.attention is not None
    assert audit.assessment.attention_share == 0.05
    assert audit.to_dict()["attention_model"] is True


@pytest.mark.asyncio
async def test_summary_mentions_contrast_and_fixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    low_contrast = _element(styles={"color": "rgb(200, 200, 200)", "font_size": "16px"})

    async def fake_capture(url: str, **kwargs: object) -> VisualSnapshot:
        snapshot = _snapshot()
        return VisualSnapshot(**{**snapshot.__dict__, "elements": (low_contrast,)})

    monkeypatch.setattr(visual_audit, "capture_visual_snapshot", fake_capture)
    monkeypatch.delenv(SALIENCY_MODEL_ENV, raising=False)

    audit = await visual_audit.run_visual_audit("https://voxlynow.com/")
    summary = "\n".join(visual_audit.format_summary(audit))

    assert "contrast" in summary
    assert "text_contrast" in summary
    assert "fix:" in summary
