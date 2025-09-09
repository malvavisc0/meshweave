import argparse
import asyncio
import json
import os
from typing import Any, Dict


def _bool_flag(val: str) -> bool:
    v = str(val).strip().lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {val}")


def _positive_int(val: str) -> int:
    i = int(val)
    if i <= 0:
        raise argparse.ArgumentTypeError("Value must be > 0")
    return i


def _nonneg_int(val: str) -> int:
    i = int(val)
    if i < 0:
        raise argparse.ArgumentTypeError("Value must be >= 0")
    return i


def _positive_float(val: str) -> float:
    f = float(val)
    if f <= 0:
        raise argparse.ArgumentTypeError("Value must be > 0")
    return f


def _write_output(payload: Dict[str, Any], output: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)


def main_crawl() -> None:
    # Lazy import to keep core import time fast in non-CLI contexts
    from .core import crawl

    p = argparse.ArgumentParser(
        prog="markdownify-crawl",
        description="Render, extract, and optionally crawl a site.",
    )
    p.add_argument("url", help="Target page URL (http/https).")
    p.add_argument(
        "--crawl-internal",
        type=_bool_flag,
        default=False,
        help="Enable internal BFS crawl (default: false)",
    )
    p.add_argument(
        "--max-pages",
        type=_positive_int,
        default=25,
        help="Max pages to visit including start page (default: 25)",
    )
    p.add_argument(
        "--same-domain",
        type=_bool_flag,
        default=True,
        help="Restrict crawl to same domain (default: true)",
    )
    p.add_argument(
        "--include-emails",
        type=_bool_flag,
        default=True,
        help="Extract emails (default: true)",
    )
    p.add_argument(
        "--deobfuscate",
        type=_bool_flag,
        default=True,
        help="Deobfuscate textual emails (default: true)",
    )
    p.add_argument(
        "--throttle-ms",
        type=_nonneg_int,
        default=0,
        help="Delay between page fetches (default: 0)",
    )
    p.add_argument(
        "--per-page-timeout",
        type=_positive_float,
        default=15.0,
        help="Timeout per crawled page (default: 15.0)",
    )
    p.add_argument(
        "--cache-dir",
        default=None,
        help="Override MARKDOWNIFY_CACHE_DIR (default: env or /tmp/markdownify/cache)",
    )
    p.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write JSON output to file (default: stdout)",
    )

    args = p.parse_args()

    async def _run():
        payload = await crawl(
            url=args.url,
            crawl_internal=bool(args.crawl_internal),
            crawl_max_pages=int(args.max_pages),
            same_domain_only=bool(args.same_domain),
            include_emails=bool(args.include_emails),
            deobfuscate_emails=bool(args.deobfuscate),
            throttle_ms=int(args.throttle_ms),
            per_page_timeout=float(args.per_page_timeout),
            cache_dir=args.cache_dir,
        )
        _write_output(payload, args.output)

    asyncio.run(_run())


def main_serve() -> None:
    try:
        import uvicorn  # type: ignore
    except Exception as e:
        raise SystemExit(
            "uvicorn is required. Install extras: pip install .[server]"
        ) from e

    # Run packaged server: markdownify_crawler.server:app
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        "markdownify_crawler.server:app",
        host=host,
        port=port,
        reload=False,
        factory=False,
    )
