"""``meshweave visual`` — capture a page and assess its primary action.

Split from :mod:`meshweave.cli` so the CLI entry module stays under the
file-size cap; the parser and runner live together here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .cli import _EPILOG, _log, _positive_float
from .visual_audit import format_summary, render_overlay, run_visual_audit


def run_visual(args: argparse.Namespace) -> None:
    """Execute the visual subcommand."""

    async def _run() -> None:
        _log(f"► Capturing {args.url} …")
        try:
            audit = await run_visual_audit(
                args.url,
                cta_text=args.cta_text,
                cdp_endpoint=args.cdp_endpoint,
                saliency_model_path=args.model,
                timeout=float(args.timeout),
            )
        except PlaywrightTimeoutError:
            _log(f"✗ Visual capture timed out for {args.url}")
            sys.exit(2)
        except (PlaywrightError, RuntimeError) as e:
            _log(f"✗ {e}")
            sys.exit(2)

        if args.as_json:
            print(json.dumps(audit.to_dict(), indent=2))
        else:
            print("\n".join(format_summary(audit)))

        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "snapshot.png").write_bytes(audit.snapshot.screenshot_png)
        overlay = render_overlay(audit)
        if overlay is not None:
            (out_dir / "attention.png").write_bytes(overlay)
        (out_dir / "report.json").write_text(json.dumps(audit.to_dict(), indent=2))
        _log(f"✓ Wrote {out_dir}/snapshot.png, attention.png, report.json")

    asyncio.run(_run())


def add_visual_parser(subs: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``visual`` subcommand on *subs*."""
    parser: argparse.ArgumentParser = subs.add_parser(
        "visual",
        help="Capture a page and assess its primary call to action",
        description=(
            "Screenshot the first viewport through Chromium and measure the "
            "main action: text and surface contrast, target size, labelling, "
            "occlusion, keyboard focus, and competing actions. With a FastSal "
            "model it also reports the action's share of predicted attention."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="Page to capture")
    parser.add_argument(
        "--cta-text",
        default=None,
        help="Action label to match; default picks the most prominent button",
    )
    parser.add_argument(
        "--cdp-endpoint",
        default=None,
        help="Chromium CDP endpoint (default: MESHWEAVE_VISUAL_CDP_ENDPOINT)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="FastSal ONNX model (default: MESHWEAVE_SALIENCY_MODEL_PATH)",
    )
    parser.add_argument(
        "--out",
        default="data/visual",
        help="Artifact directory (default: data/visual)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the assessment as JSON instead of a table",
    )
    parser.add_argument(
        "--timeout", type=_positive_float, default=30.0, help="Capture timeout"
    )
    parser.set_defaults(func=run_visual)
    return parser
