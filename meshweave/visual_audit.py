"""One-shot visual CTA audit: capture, attention, deterministic assessment.

Shared by the CLI (``meshweave visual``) and the v1 API so both surfaces
report the same numbers from the same pipeline.
"""

from __future__ import annotations

import asyncio
import io
import os
from dataclasses import dataclass
from typing import Any

from .crawling.visual import (
    DEFAULT_VIEWPORT,
    VisualElement,
    VisualSnapshot,
    capture_visual_snapshot,
)
from .scoring.cta_visual import CtaVisualAssessment, assess_visual_cta
from .scoring.saliency import SALIENCY_MODEL_ENV, SaliencyMap, load_saliency_model

# Bumped when the capture or assessment pipeline changes shape, so stored
# audits can be told apart from re-captured ones.
VISUAL_CAPTURE_VERSION = "1"


@dataclass(frozen=True)
class VisualAudit:
    """Everything one visual pass measured for a page."""

    snapshot: VisualSnapshot
    attention: SaliencyMap | None
    assessment: CtaVisualAssessment

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.snapshot.final_url,
            "viewport": {
                "width": self.snapshot.viewport_width,
                "height": self.snapshot.viewport_height,
            },
            "element_count": len(self.snapshot.elements),
            "screenshot_bytes": len(self.snapshot.screenshot_png),
            "attention_model": self.attention is not None,
            "assessment": self.assessment.to_dict(),
        }


async def run_visual_audit(
    url: str,
    *,
    cta_text: str | None = None,
    cdp_endpoint: str | None = None,
    saliency_model_path: str | None = None,
    viewport: tuple[int, int] = DEFAULT_VIEWPORT,
    timeout: float = 30.0,
) -> VisualAudit:
    """Capture *url* and assess its primary call to action.

    Attention is computed only when a FastSal model path is given or
    ``MESHWEAVE_SALIENCY_MODEL_PATH`` is set; otherwise the deterministic
    assessment runs alone. Model loading and inference run off the event
    loop; the loaded session is cached per model path.
    """
    snapshot = await capture_visual_snapshot(
        url,
        cdp_endpoint=cdp_endpoint,
        viewport=viewport,
        timeout=timeout,
    )
    model_path = saliency_model_path or os.environ.get(SALIENCY_MODEL_ENV)
    attention = None
    if model_path:
        model = await asyncio.to_thread(load_saliency_model, model_path)
        attention = await asyncio.to_thread(model.predict, snapshot.screenshot_png)
    assessment = assess_visual_cta(snapshot, cta_text=cta_text, saliency=attention)
    return VisualAudit(snapshot=snapshot, attention=attention, assessment=assessment)


def render_overlay(audit: VisualAudit) -> bytes | None:
    """Return the screenshot with the attention heatmap and CTA box.

    ``None`` when the audit has no attention map. Needs the ``saliency``
    extra (numpy, pillow).
    """
    if audit.attention is None:
        return None
    import numpy as np
    from PIL import Image, ImageDraw

    base = Image.open(io.BytesIO(audit.snapshot.screenshot_png)).convert("RGB")
    heat = np.asarray(audit.attention.values, dtype=np.float32)
    heat = heat / max(float(heat.max()), 1e-6)
    alpha = (heat * 0.55)[..., None]
    tint = np.zeros((*heat.shape, 3), dtype=np.float32)
    tint[..., 0] = 1.0
    tint[..., 1] = np.clip(1.0 - heat * 1.6, 0, 1)
    composed = np.asarray(base, dtype=np.float32) * (1 - alpha) + tint * 255 * alpha
    image = Image.fromarray(composed.clip(0, 255).astype("uint8"))
    element = audit.assessment.element
    if element is not None:
        ImageDraw.Draw(image).rectangle(
            (
                element.x,
                element.y,
                element.x + element.width,
                element.y + element.height,
            ),
            outline=(0, 255, 120),
            width=3,
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _flag(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _metric_lines(element: VisualElement, assessment: CtaVisualAssessment) -> list[str]:
    ratio = assessment.contrast_ratio
    threshold = assessment.contrast_threshold
    contrast = (
        f"{ratio:.2f}:1 (needs {threshold:.1f}) {_flag(ratio >= threshold)}"
        if ratio is not None
        else "n/a"
    )
    surface = (
        f"{assessment.surface_contrast:.2f}:1 "
        f"{_flag(assessment.surface_contrast >= 3.0)}"
        if assessment.surface_contrast is not None
        else "transparent"
    )
    attention = (
        f"{assessment.attention_share * 100:.1f}% of predicted mass "
        f"(rank {assessment.attention_rank} of "
        f"{assessment.competing_actions + 1} actions)"
        if assessment.attention_share is not None
        else "n/a"
    )
    return [
        f"  box         {element.width}x{element.height}px at "
        f"({element.x}, {element.y})",
        f"  contrast    {contrast}",
        f"  surface     {surface}",
        f"  target      {assessment.target_px}px {_flag(assessment.target_px >= 24)}",
        f"  above fold  {_flag(element.above_fold)}",
        f"  focusable   {_flag(element.focusable)}",
        f"  named       {_flag(element.has_accessible_name)}",
        f"  occluded    {'YES' if element.occluded else 'no'}",
        f"  attention   {attention}",
        f"  rivals      {', '.join(assessment.competing_names) or 'none'}",
    ]


def format_summary(audit: VisualAudit) -> list[str]:
    """Human-readable report lines, shared by the CLI and the smoke script."""
    snapshot = audit.snapshot
    assessment = audit.assessment
    lines = [
        f"URL          {snapshot.url}",
        f"final        {snapshot.final_url}",
        f"viewport     {snapshot.viewport_width}x{snapshot.viewport_height}"
        f" (scroll {snapshot.scroll_height}px)",
        f"elements     {len(snapshot.elements)} visible interactive",
        "",
        "CTA",
    ]
    if assessment.element is None:
        lines.append("  none found in the first viewport")
    else:
        lines.append(
            f"  label       {assessment.element.name!r} ({assessment.matched_by})"
        )
        lines.extend(_metric_lines(assessment.element, assessment))
    lines.extend(["", "Findings"])
    if not assessment.findings:
        lines.append("  none")
    else:
        for finding in assessment.findings:
            lines.append(
                f"  [{finding.severity}] {finding.code}: {finding.detail}\n"
                f"    fix: {finding.fix}"
            )
    return lines
