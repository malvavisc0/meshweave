import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()


def _positive_int(val: str) -> int:
    """Parse a string into a strictly positive integer (> 0)."""
    i = int(val)
    if i <= 0:
        raise argparse.ArgumentTypeError("Value must be > 0")
    return i


def _nonneg_int(val: str) -> int:
    """Parse a string into a non-negative integer (>= 0)."""
    i = int(val)
    if i < 0:
        raise argparse.ArgumentTypeError("Value must be >= 0")
    return i


def _positive_float(val: str) -> float:
    """Parse a string into a strictly positive float (> 0)."""
    f = float(val)
    if f <= 0:
        raise argparse.ArgumentTypeError("Value must be > 0")
    return f


def _md_file_path(url: str, output_dir: str) -> str:
    """Map a URL to a local file path under *output_dir*.

    Example::

        _md_file_path("https://example.com/about", "data/output")
        # => "data/output/example.com/about.md"

        _md_file_path("https://example.com/", "data/output")
        # => "data/output/example.com/index.md"
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    # hostname is None for malformed/hostless URLs; fall back to a safe label.
    domain = (parsed.hostname or "unknown-host").lower()
    path = parsed.path.rstrip("/") or "/"

    if path == "/":
        return str(Path(output_dir) / domain / "index.md")

    # Strip leading slash and split into dir parts + filename
    parts = path.lstrip("/").split("/")
    filename = parts[-1] + ".md"
    dirpath = Path(output_dir) / domain
    for d in parts[:-1]:
        dirpath = dirpath / d
    return str(dirpath / filename)


def _write_markdown_files(payload: dict[str, Any], output_dir: str) -> None:
    """Write markdown content to files, replacing inline text with paths."""
    markdowns = payload.get("markdowns", {})

    output_dir = str(Path(output_dir).resolve())
    payload["markdown_dir"] = output_dir

    if not markdowns:
        return

    # Write each page's markdown to a file
    written: dict[str, Any] = {}
    for url, entry in markdowns.items():
        if not entry:
            continue
        # Support both old (str) and new (dict) format
        if isinstance(entry, dict):
            md = entry.get("markdown", "")
            page_meta = entry.get("page", {})
        else:
            md = str(entry)
            page_meta = {}
        if not md:
            continue
        fpath = _md_file_path(url, output_dir)
        Path(fpath).parent.mkdir(parents=True, exist_ok=True)
        Path(fpath).write_text(md, encoding="utf-8")
        abs_path = str(Path(fpath).resolve())
        rel = str(Path(fpath).relative_to(output_dir)).replace(".md", "")
        page_entry: dict[str, Any] = {
            "path": abs_path.replace(output_dir, ""),
            "lines": md.count("\n") + 1,
            "size_bytes": len(md.encode("utf-8")),
            "page": page_meta,
        }
        if entry.get("headings"):
            page_entry["headings"] = entry["headings"]
        if entry.get("content_metrics"):
            page_entry["content_metrics"] = entry["content_metrics"]
        written[rel] = page_entry

    payload["markdowns"] = written


def _write_output(payload: dict[str, Any], output: str) -> None:
    """Write JSON *payload* to *output* file."""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    with open(output, "w", encoding="utf-8") as f:
        f.write(text)


def _add_crawl_args(sub: argparse.ArgumentParser) -> None:
    """Add crawl-specific arguments to *sub*."""
    sub.add_argument(
        "url",
        help=(
            "Target URL or bare domain (e.g. example.com). "
            "Bare domains are expanded to https://. The URL path "
            "defines the crawl scope — only pages under that prefix "
            "are treated as internal."
        ),
    )

    sub.add_argument(
        "--max-pages",
        type=_positive_int,
        default=25,
        metavar="N",
        help="Max pages to visit including start page (default: 25)",
    )

    sub.add_argument(
        "--max-depth",
        type=_nonneg_int,
        default=1,
        metavar="N",
        help="Max link depth from start page (default: 1, 0=unlimited)",
    )

    sub.add_argument(
        "--ai-analysis",
        action="store_true",
        default=False,
        dest="ai_analysis",
        help="Run LLM-powered AAX (AI Agent Experience) analysis after crawl",
    )
    sub.add_argument(
        "--no-ai-analysis",
        action="store_false",
        dest="ai_analysis",
        help="Skip AAX analysis (default)",
    )

    sub.add_argument(
        "--include-emails",
        action="store_true",
        default=True,
        dest="include_emails",
        help="Extract emails (default: on)",
    )
    sub.add_argument(
        "--no-include-emails",
        action="store_false",
        dest="include_emails",
        help="Skip email extraction",
    )

    sub.add_argument(
        "--deobfuscate",
        action="store_true",
        default=True,
        dest="deobfuscate",
        help="Deobfuscate textual emails (default: on)",
    )
    sub.add_argument(
        "--no-deobfuscate",
        action="store_false",
        dest="deobfuscate",
        help="Do not deobfuscate emails",
    )

    sub.add_argument(
        "--throttle-ms",
        type=_nonneg_int,
        default=0,
        metavar="MS",
        help="Delay between page fetches in milliseconds (default: 0)",
    )

    sub.add_argument(
        "--per-page-timeout",
        type=_positive_float,
        default=15.0,
        metavar="SEC",
        help="Timeout per crawled page in seconds (default: 15.0)",
    )

    sub.add_argument(
        "--disable-cache",
        action="store_true",
        default=False,
        dest="disable_cache",
        help="Bypass HTML cache for this run (default: off)",
    )

    sub.add_argument(
        "--cache-dir",
        default=None,
        metavar="DIR",
        help="Override MESHWEAVE_CACHE_DIR",
    )

    sub.add_argument(
        "--output",
        "-o",
        required=True,
        metavar="FILE",
        help="Write JSON output to FILE (required)",
    )

    sub.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help=(
            "Directory for markdown files (default: data/output/). "
            "Files are organized as domain/path.md"
        ),
    )

    sub.add_argument(
        "--refresh",
        action="store_true",
        default=False,
        dest="refresh",
        help="Clear cached HTML and output .md files before re-crawling",
    )
    sub.add_argument(
        "--no-refresh",
        action="store_false",
        dest="refresh",
        help="Use existing cache (default)",
    )


def _log(msg: str) -> None:
    """Print a status message to stderr."""
    print(msg, file=sys.stderr)


def _run_crawl(args: argparse.Namespace) -> None:
    """Execute the crawl subcommand."""

    from .core import crawl  # lazy import

    async def _run():
        _log(f"► Crawling {args.url} …")

        # --refresh implies --disable-cache
        disable_cache = args.disable_cache or args.refresh

        _log("► Rendering page with CloakBrowser …")
        payload = await crawl(
            url=args.url,
            crawl_max_pages=int(args.max_pages),
            max_depth=int(args.max_depth),
            include_emails=args.include_emails,
            deobfuscate_emails=args.deobfuscate,
            throttle_ms=int(args.throttle_ms),
            per_page_timeout=float(args.per_page_timeout),
            disable_cache=disable_cache,
            cache_dir=args.cache_dir,
        )

        # Summarise render result
        render = payload.get("metrics", {}).get("render", {})
        status = render.get("response_status", "?")
        final_url = render.get("final_url", args.url)
        load_ms = render.get("load_time_ms", 0)
        _log(f"  ✓ Page rendered — {status} {final_url} ({load_ms}ms)")

        output_dir = args.output_dir
        if not output_dir:
            output_dir = os.getenv("MESHWEAVE_OUTPUT_DIR", "data/output")

        # --refresh clears existing .md files for this domain
        if args.refresh:
            from urllib.parse import urlparse

            domain = (
                urlparse(payload.get("crawl", {}).get("start_url", "")).hostname or ""
            ).lower()
            if domain:
                import shutil

                domain_dir = Path(output_dir) / domain
                if domain_dir.exists():
                    shutil.rmtree(domain_dir)
                    _log(f"  ✓ Refreshed cache: cleared {domain_dir}")

        # Always compute AEO/GEO scores (pure heuristic, no LLM)
        _log("► Computing AEO/GEO scores …")
        try:
            from meshweave.scoring.engine import compute_scores

            payload["scores"] = compute_scores(payload)
            _log("  ✓ Scores computed")
        except Exception as e:
            print(f"  ✗ Scoring failed: {e}", file=sys.stderr)
            payload["scores"] = None

        # AAX analysis (LLM-powered, only with --ai-analysis flag)
        if args.ai_analysis:
            _log("► Running AAX analysis (LLM) …")
            try:
                # Override AAX_ENABLED since CLI flag is the explicit opt-in
                os.environ["AAX_ENABLED"] = "true"
                from meshweave.ai.analyses import run_aax_analysis

                aax_result = await run_aax_analysis(payload)
                payload["aax"] = aax_result

                # Compute AAX composite and merge into scores
                if aax_result and aax_result.get("status") == "completed":
                    from meshweave.scoring.engine import compute_aax_score

                    aax_score = compute_aax_score(aax_result)
                    if aax_score and payload.get("scores"):
                        payload["scores"]["aax"] = aax_score
                _log("  ✓ AAX analysis complete")
            except Exception as e:
                print(f"  ✗ AAX analysis failed: {e}", file=sys.stderr)
                payload["aax"] = {"status": "failed", "error": str(e)}
        else:
            payload["aax"] = None

        _log(f"► Writing markdown files to {output_dir} …")
        _write_markdown_files(payload, output_dir)
        n_pages = len(payload.get("crawl", {}).get("visited", []))
        _log(f"  ✓ {n_pages} page(s) written")

        _log(f"► Writing JSON to {args.output} …")
        _write_output(payload, args.output)

        _log(f"✓ Done — {n_pages} page(s) visited, output written to {args.output}")

    asyncio.run(_run())


_EPILOG = """\
examples:
  meshweave crawl https://example.com -o result.json
  meshweave crawl https://example.com --max-pages 50 -o out.json
  meshweave crawl example.com --throttle-ms 200 -o result.json
"""


def main() -> None:
    """CLI entry point.

    If the first positional argument is not a known subcommand (``crawl``),
    ``crawl`` is assumed so that ``meshweave <url> …`` keeps working as
    before.
    """
    # When called with no arguments, show help so the user can see the
    # available subcommands.  When the first argument is a URL (i.e. not a
    # known subcommand), default to "crawl" for backward compatibility
    # with  meshweave <url> [flags].
    known_subcommands = {"crawl", "--help", "-h", "--version"}
    if len(sys.argv) < 2:
        sys.argv.insert(1, "--help")
    elif sys.argv[1] not in known_subcommands:
        sys.argv.insert(1, "crawl")

    parser = argparse.ArgumentParser(
        prog="meshweave",
        description=(
            "Render pages with CloakBrowser, extract markdown, links, "
            "emails — and optionally crawl internal links."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subs = parser.add_subparsers(dest="subcommand", help="Available commands")

    # -- crawl --
    crawl_parser = subs.add_parser(
        "crawl",
        help="Render, extract, and optionally crawl a site",
        description=(
            "Fetch a URL (or bare domain) with CloakBrowser, extract "
            "markdown, links, and emails.  Always crawls internal links "
            "within the URL's path scope."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_crawl_args(crawl_parser)
    crawl_parser.set_defaults(func=_run_crawl)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
