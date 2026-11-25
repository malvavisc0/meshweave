"""Smoke-test the visual CTA pipeline against a live page.

Captures a Chromium screenshot, computes the FastSal attention map, runs the
deterministic CTA assessment, and writes the screenshot, an attention overlay,
and the JSON report to disk.

Requires a Chromium CDP endpoint and a FastSal ONNX model. Fetch the model
once (Apache-2.0, 10.2 MB, sha256
6beb2db6820d5609746905d0b6d0df953aaa42c84848b9067ebb11798a675148)::

    curl -L -o /tmp/fastsal.onnx \\
      https://raw.githubusercontent.com/feiyanhu/FastSal/master/onnx/saliconC/fastsal.onnx

    MESHWEAVE_VISUAL_CDP_ENDPOINT=http://127.0.0.1:9222 \\
    MESHWEAVE_SALIENCY_MODEL_PATH=/tmp/fastsal.onnx \\
    uv run python scripts/smoke_visual.py https://example.com
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from meshweave.visual_audit import (
    VisualAudit,
    format_summary,
    render_overlay,
    run_visual_audit,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Page to capture")
    parser.add_argument(
        "--cdp-endpoint",
        default=os.environ.get("MESHWEAVE_VISUAL_CDP_ENDPOINT"),
        help="Chromium CDP endpoint (default: MESHWEAVE_VISUAL_CDP_ENDPOINT)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("MESHWEAVE_SALIENCY_MODEL_PATH"),
        help="FastSal ONNX model (default: MESHWEAVE_SALIENCY_MODEL_PATH)",
    )
    parser.add_argument(
        "--cta-text",
        default=None,
        help="Action label to match; omit to pick the most prominent button",
    )
    parser.add_argument(
        "--out", default="data/smoke", help="Output directory (default: data/smoke)"
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    if not args.cdp_endpoint:
        print(
            "Set --cdp-endpoint or MESHWEAVE_VISUAL_CDP_ENDPOINT "
            "(compose service 'chromium').",
            file=sys.stderr,
        )
        return 2
    if not args.model:
        print(
            "Set --model or MESHWEAVE_SALIENCY_MODEL_PATH to a FastSal ONNX file.",
            file=sys.stderr,
        )
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Capturing {args.url} ...")
    audit = await run_visual_audit(
        args.url,
        cta_text=args.cta_text,
        cdp_endpoint=args.cdp_endpoint,
        saliency_model_path=args.model,
        timeout=args.timeout,
    )
    print("\n".join(format_summary(audit)))
    overlay = render_overlay(audit)
    assert overlay is not None, "attention overlay missing"
    _check(audit, overlay)

    (out_dir / "snapshot.png").write_bytes(audit.snapshot.screenshot_png)
    (out_dir / "attention.png").write_bytes(overlay)
    payload: dict[str, Any] = audit.to_dict()
    payload["snapshot"] = audit.snapshot.to_dict()
    (out_dir / "report.json").write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {out_dir}/snapshot.png, attention.png, report.json")
    return 0


def _check(audit: VisualAudit, overlay: bytes) -> None:
    """Fail loudly when a pipeline invariant is broken."""
    assert audit.snapshot.screenshot_png[:8] == PNG_MAGIC, "not a PNG screenshot"
    assert overlay[:8] == PNG_MAGIC, "not a PNG overlay"
    focus = audit.assessment.element
    assert focus is not None, "no CTA element found in the first viewport"
    assert focus.width > 0 and focus.height > 0, "degenerate CTA box"
    assert focus.x < audit.snapshot.viewport_width, "CTA outside the screenshot"
    assert focus.y < audit.snapshot.viewport_height, "CTA outside the screenshot"
    attention = audit.attention
    assert attention is not None, "attention map missing"
    total = float(attention.values.sum())
    assert abs(total - 1.0) < 1e-3, f"attention map not normalised: {total}"
    assert attention.width == audit.snapshot.viewport_width, "map/page width mismatch"
    assert attention.height == audit.snapshot.viewport_height, (
        "map/page height mismatch"
    )


def main() -> None:
    args = _parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
