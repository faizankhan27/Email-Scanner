"""Background scan jobs for the web app."""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..core import EmailResult, Guard, Progress, scan

log = logging.getLogger(__name__)

FINISHED = ("done", "error", "cancelled")


class Busy(Exception):
    """Raised when too many scans are already running."""


def result_dict(r: EmailResult) -> dict:
    return {
        "email": r.email,
        "best_page": r.best_page,
        "found_as": r.found_as.split("/"),
        "pages_found_on": r.pages_found_on,
        "site_domain": r.site_domain,
        "all_pages": r.all_pages,
    }


@dataclass
class Job:
    id: str
    url: str
    max_pages: int
    delay: float
    status: str = "queued"
    pages_scanned: int = 0
    pages_discovered: int = 1
    current_url: str = ""
    results: List[EmailResult] = field(default_factory=list)
    error: Optional[str] = None
    stopped_reason: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    stop: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def finished(self) -> bool:
        return self.status in FINISHED

    def to_dict(self, include_results: bool = True) -> dict:
        now = time.time()
        elapsed = 0.0
        if self.started_at is not None:
            elapsed = max(0.0, (self.finished_at or now) - self.started_at)
        data = {
            "id": self.id,
            "url": self.url,
            "status": self.status,
            "finished": self.finished,
            "max_pages": self.max_pages,
            "pages_scanned": self.pages_scanned,
            "pages_discovered": self.pages_discovered,
            "current_url": self.current_url,
            "email_count": len(self.results),
            "elapsed": round(elapsed, 1),
            "error": self.error,
            "stopped_reason": self.stopped_reason,
        }
        if include_results:
            data["results"] = [result_dict(r) for r in list(self.results)]
        return data


class JobManager:
    """Starts scans on background threads and keeps their state in memory."""

    def __init__(
        self,
        guard: Optional[Guard] = None,
        max_concurrent: int = 2,
        max_jobs: int = 100,
        ttl_seconds: int = 3600,
        max_seconds: Optional[float] = 600,
        timeout: float = 10,
    ) -> None:
        self.guard = guard
        self.max_concurrent = max_concurrent
        self.max_jobs = max_jobs
        self.ttl_seconds = ttl_seconds
        self.max_seconds = max_seconds
        self.timeout = timeout
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, url: str, max_pages: int, delay: float) -> Job:
        with self._lock:
            self._evict_locked()
            active = sum(1 for j in self._jobs.values() if not j.finished)
            if active >= self.max_concurrent:
                raise Busy()
            job = Job(
                id=secrets.token_urlsafe(12),
                url=url,
                max_pages=max_pages,
                delay=delay,
            )
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Optional[Job]:
        job = self.get(job_id)
        if job is not None:
            job.stop.set()
        return job

    def _evict_locked(self) -> None:
        now = time.time()
        for job_id, job in list(self._jobs.items()):
            if job.finished and now - (job.finished_at or now) > self.ttl_seconds:
                del self._jobs[job_id]
        if len(self._jobs) >= self.max_jobs:
            finished = sorted(
                (j for j in self._jobs.values() if j.finished),
                key=lambda j: j.finished_at or 0,
            )
            for job in finished[: len(self._jobs) - self.max_jobs + 1]:
                del self._jobs[job.id]

    def _run(self, job: Job) -> None:
        job.status = "running"
        job.started_at = time.time()

        def on_update(p: Progress) -> None:
            job.pages_scanned = p.pages_scanned
            job.pages_discovered = p.pages_discovered
            job.current_url = p.current_url
            job.results = p.results

        try:
            result = scan(
                job.url,
                max_pages=job.max_pages,
                delay=job.delay,
                respect_robots=True,
                on_update=on_update,
                should_stop=job.stop.is_set,
                guard=self.guard,
                max_seconds=self.max_seconds,
                timeout=self.timeout,
            )
            job.pages_scanned = result.pages_scanned
            job.results = result.results
            job.stopped_reason = result.stopped_reason
            if result.stopped_reason == "cancelled":
                job.status = "cancelled"
            elif result.pages_scanned == 0:
                job.status = "error"
                if result.skipped_by_robots:
                    job.error = (
                        "This site's robots.txt asks scanners to stay out, "
                        "so nothing was scanned."
                    )
                else:
                    job.error = (
                        "No pages could be read. Check the address, or the site "
                        "may be down or blocking automated visits."
                    )
            else:
                job.status = "done"
        except Exception:  # pragma: no cover - safety net
            log.exception("Scan failed")
            job.status = "error"
            job.error = "Something went wrong while scanning. Please try again."
        finally:
            job.finished_at = time.time()
