"""Command line interface."""

from __future__ import annotations

import argparse
import re
import sys
from typing import List, Optional

from . import __version__
from .core import scan, stderr_progress
from .report import write_csv, write_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="email-scanner",
        description="List every email on a website with the best page for each.",
    )
    parser.add_argument("url", help="Start URL, for example https://example.com")
    parser.add_argument(
        "--max-pages", type=int, default=200, help="Maximum pages to crawl (default 200)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds to wait between requests (default 0.5)",
    )
    parser.add_argument(
        "--output",
        default="emails_found.csv",
        help="CSV file to write (default emails_found.csv)",
    )
    parser.add_argument(
        "--markdown", metavar="FILE", help="Also write a Markdown summary table to FILE"
    )
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="Do not read robots.txt (only use this on sites you own)",
    )
    parser.add_argument("--quiet", action="store_true", help="Hide per page progress")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    url = args.url if re.match(r"^https?://", args.url) else "https://" + args.url
    result = scan(
        url,
        max_pages=args.max_pages,
        delay=args.delay,
        respect_robots=not args.ignore_robots,
        progress=None if args.quiet else stderr_progress,
    )

    write_csv(result.results, args.output)
    if args.markdown:
        write_markdown(
            result.results, result.pages_scanned, result.root_host, args.markdown
        )

    print(
        f"\nScanned {result.pages_scanned} pages, "
        f"found {len(result.results)} unique emails.\n"
    )
    for row in result.results:
        print(
            f"{row.email}\n    {row.best_page}  "
            f"({row.found_as}, on {row.pages_found_on} page(s))"
        )
    print(f"\nSaved to {args.output}")

    if result.pages_scanned == 0:
        print(
            "No pages could be fetched. Check the URL, your connection, "
            "or the site's robots.txt.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
