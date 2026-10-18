"""Core logic: crawling a site, extracting emails and choosing the best page."""

from __future__ import annotations

import re
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Dict, List, Optional, Set
from urllib import robotparser
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urldefrag, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = "EmailScanner/1.1 (+site email audit)"

EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
)
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*[\[\(\{]\s*at\s*[\]\)\}]\s*"
    r"([A-Za-z0-9\-]+(?:\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*[A-Za-z0-9\-]+)+)",
    re.I,
)
DOT_RE = re.compile(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", re.I)

FAKE_TLDS = {
    "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "css", "js",
    "woff", "woff2", "ttf", "eot", "pdf", "mp4", "mp3", "zip",
}
SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js",
    ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".zip", ".mp4", ".mp3",
    ".avi", ".mov", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".xml", ".json", ".rss", ".gz", ".exe", ".dmg",
)

CONTACT_PATH = re.compile(
    r"contact|reach|get[-_]?in[-_]?touch|support|enquir|inquir|hello"
)
ABOUT_PATH = re.compile(r"about|team|people|staff|imprint|impressum|leadership")

# A guard receives every address before it is fetched and raises ValueError
# (for example UnsafeURL) to refuse it.
Guard = Callable[[str], None]


@dataclass
class Sighting:
    """One email seen on one page."""

    page_url: str
    sources: Set[str]
    depth: int
    emails_on_page: int


@dataclass
class EmailResult:
    """Final row for one email."""

    email: str
    best_page: str
    found_as: str
    pages_found_on: int
    all_pages: List[str]
    site_domain: bool


@dataclass
class Progress:
    """Snapshot handed to the on_update callback after each page."""

    pages_scanned: int
    pages_discovered: int
    current_url: str
    results: List[EmailResult]


@dataclass
class ScanResult:
    pages_scanned: int
    root_host: str
    results: List[EmailResult] = field(default_factory=list)
    stopped_reason: Optional[str] = None
    skipped_by_robots: int = 0


def decode_cfemail(encoded: str) -> Optional[str]:
    """Decode a Cloudflare protected email (data-cfemail value)."""
    try:
        key = int(encoded[:2], 16)
        return "".join(
            chr(int(encoded[i:i + 2], 16) ^ key) for i in range(2, len(encoded), 2)
        )
    except (ValueError, IndexError):
        return None


class PageParser(HTMLParser):
    """Collects links, mailto targets, protected emails and visible text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: List[str] = []
        self.mailtos: List[str] = []
        self.cf_emails: List[str] = []
        self.text_parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
        href = (attrs.get("href") or "").strip()
        if tag == "a" and href:
            if href.lower().startswith("mailto:"):
                self.mailtos.append(href[7:])
            else:
                self.links.append(href)
        if attrs.get("data-cfemail"):
            decoded = decode_cfemail(attrs["data-cfemail"])
            if decoded:
                self.cf_emails.append(decoded)
        if href.startswith("/cdn-cgi/l/email-protection#"):
            decoded = decode_cfemail(href.split("#", 1)[1])
            if decoded:
                self.cf_emails.append(decoded)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self.text_parts.append(data)


def clean_email(raw: str) -> Optional[str]:
    """Normalise a candidate email, or return None if it is not a real one."""
    email = unquote(raw).strip().strip(".,;:<>()[]\"'").lower()
    if "@" not in email:
        return None
    tld = email.rsplit(".", 1)[-1]
    if tld in FAKE_TLDS or not EMAIL_RE.fullmatch(email):
        return None
    return email


def extract_emails(html: str):
    """Return ({email: set of source types}, links) for one page."""
    parser = PageParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    found: Dict[str, Set[str]] = defaultdict(set)

    for raw in parser.mailtos:
        for part in raw.split("?")[0].split(","):
            email = clean_email(part)
            if email:
                found[email].add("mailto")

    for raw in parser.cf_emails:
        email = clean_email(raw)
        if email:
            found[email].add("protected")

    text = " ".join(parser.text_parts)
    for match in EMAIL_RE.findall(text):
        email = clean_email(match)
        if email:
            found[email].add("text")

    for user, domain in OBFUSCATED_RE.findall(text):
        email = clean_email(user + "@" + DOT_RE.sub(".", domain))
        if email:
            found[email].add("obfuscated")

    return found, parser.links


def normalise(url: str) -> str:
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    return parsed._replace(path=parsed.path or "/", fragment="").geturl()


def same_site(url: str, root_host: str) -> bool:
    def strip(host: str) -> str:
        return host[4:] if host.startswith("www.") else host

    return strip(urlparse(url).netloc.lower()) == strip(root_host.lower())


class _GuardedRedirects(HTTPRedirectHandler):
    """Check every redirect target with the guard before following it."""

    def __init__(self, guard: Optional[Guard]) -> None:
        super().__init__()
        self._guard = guard

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self._guard is not None:
            self._guard(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(
    url: str,
    timeout: float = 15,
    max_bytes: int = 2_000_000,
    guard: Optional[Guard] = None,
    require_html: bool = True,
) -> Optional[str]:
    """Download a page and return its text, or None if it is not usable."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        if guard is not None:
            guard(url)
        opener = build_opener(_GuardedRedirects(guard))
        with opener.open(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if require_html and "html" not in content_type.lower():
                return None
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read(max_bytes).decode(charset, errors="replace")
    except (HTTPError, URLError, TimeoutError, ValueError, OSError, LookupError):
        return None


def page_score(page_url: str, sources: Set[str], depth: int, emails_on_page: int):
    """Rank a page as the home for an email. Higher tuples win."""
    score = 0
    if "mailto" in sources:
        score += 4
    if "text" in sources:
        score += 2
    if sources & {"protected", "obfuscated"}:
        score += 2
    path = urlparse(page_url).path.lower()
    if CONTACT_PATH.search(path):
        score += 3
    elif ABOUT_PATH.search(path):
        score += 1
    if emails_on_page <= 3:
        score += 1
    return (score, -depth)


def build_results(
    sightings: Dict[str, List[Sighting]], root_host: str
) -> List[EmailResult]:
    base = root_host[4:] if root_host.startswith("www.") else root_host
    base = base.split(":", 1)[0]
    results = []
    for email, seen_on in sightings.items():
        best = max(
            seen_on,
            key=lambda s: page_score(s.page_url, s.sources, s.depth, s.emails_on_page),
        )
        results.append(
            EmailResult(
                email=email,
                best_page=best.page_url,
                found_as="/".join(sorted(best.sources)),
                pages_found_on=len({s.page_url for s in seen_on}),
                all_pages=sorted({s.page_url for s in seen_on}),
                site_domain=email.endswith("@" + base) or email.endswith("." + base),
            )
        )
    results.sort(key=lambda r: (not r.site_domain, r.email))
    return results


def _pause(seconds: float, should_stop: Optional[Callable[[], bool]]) -> None:
    """Sleep in short steps so a stop request is noticed quickly."""
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0 or (should_stop is not None and should_stop()):
            return
        time.sleep(min(0.1, remaining))


def scan(
    start_url: str,
    max_pages: int = 200,
    delay: float = 0.5,
    respect_robots: bool = True,
    progress: Optional[Callable[[int, str], None]] = None,
    on_update: Optional[Callable[[Progress], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    guard: Optional[Guard] = None,
    max_seconds: Optional[float] = None,
    timeout: float = 15,
) -> ScanResult:
    """Crawl one site and return every email with its best page."""
    start_url = normalise(start_url)
    root_host = urlparse(start_url).netloc.lower()
    started = time.monotonic()

    robots = None
    if respect_robots:
        text = fetch(
            urljoin(start_url, "/robots.txt"),
            timeout=timeout,
            guard=guard,
            require_html=False,
        )
        if text:
            robots = robotparser.RobotFileParser()
            robots.parse(text.splitlines())

    queue = deque([(start_url, 0)])
    seen = {start_url}
    sightings: Dict[str, List[Sighting]] = defaultdict(list)
    pages_scanned = 0
    skipped_by_robots = 0
    stopped_reason: Optional[str] = None

    while queue and pages_scanned < max_pages:
        if should_stop is not None and should_stop():
            stopped_reason = "cancelled"
            break
        if max_seconds is not None and time.monotonic() - started > max_seconds:
            stopped_reason = "time limit"
            break

        url, depth = queue.popleft()
        if robots and not robots.can_fetch(USER_AGENT, url):
            skipped_by_robots += 1
            continue

        html = fetch(url, timeout=timeout, guard=guard)
        if html is None:
            continue
        pages_scanned += 1

        found, links = extract_emails(html)
        for email, sources in found.items():
            sightings[email].append(Sighting(url, sources, depth, len(found)))

        for href in links:
            absolute = normalise(urljoin(url, href))
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.path.lower().endswith(SKIP_EXTENSIONS):
                continue
            if not same_site(absolute, root_host) or absolute in seen:
                continue
            seen.add(absolute)
            queue.append((absolute, depth + 1))

        if progress:
            progress(pages_scanned, url)
        if on_update:
            on_update(
                Progress(
                    pages_scanned=pages_scanned,
                    pages_discovered=len(seen),
                    current_url=url,
                    results=build_results(sightings, root_host),
                )
            )

        if delay and queue and pages_scanned < max_pages:
            _pause(delay, should_stop)

    return ScanResult(
        pages_scanned=pages_scanned,
        root_host=root_host,
        results=build_results(sightings, root_host),
        stopped_reason=stopped_reason,
        skipped_by_robots=skipped_by_robots,
    )


def stderr_progress(count: int, url: str) -> None:
    print(f"[{count}] {url}", file=sys.stderr)
