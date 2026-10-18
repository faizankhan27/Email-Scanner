"""Output writers for scan results."""

from __future__ import annotations

import csv
import io
from typing import List

from .core import EmailResult

CSV_FIELDS = [
    "email",
    "best_page",
    "found_as",
    "pages_found_on",
    "site_domain",
    "all_pages",
]

# Spreadsheet programs treat cells that start with these characters as formulas.
# The text comes from websites we do not control, so neutralise it.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def _row(r: EmailResult) -> dict:
    return {
        "email": safe_cell(r.email),
        "best_page": safe_cell(r.best_page),
        "found_as": r.found_as,
        "pages_found_on": r.pages_found_on,
        "site_domain": "yes" if r.site_domain else "no",
        "all_pages": safe_cell(" | ".join(r.all_pages)),
    }


def to_csv_text(results: List[EmailResult]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for r in results:
        writer.writerow(_row(r))
    return buffer.getvalue()


def write_csv(results: List[EmailResult], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        handle.write(to_csv_text(results))


def to_markdown(results: List[EmailResult], pages_scanned: int, host: str) -> str:
    lines = [
        f"## Email scan for {host}",
        "",
        f"Scanned {pages_scanned} pages and found {len(results)} unique emails.",
        "",
    ]
    if results:
        lines += [
            "| Email | Best page | Found as | Pages |",
            "| --- | --- | --- | --- |",
        ]
        for r in results:
            email = r.email.replace("|", "\\|")
            lines.append(
                f"| {email} | {r.best_page} | {r.found_as} | {r.pages_found_on} |"
            )
    else:
        lines.append("No emails were found.")
    return "\n".join(lines) + "\n"


def write_markdown(
    results: List[EmailResult], pages_scanned: int, host: str, path: str
) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(to_markdown(results, pages_scanned, host))
