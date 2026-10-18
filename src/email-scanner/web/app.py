"""Flask web app: a page to start scans, watch them run and download results."""

from __future__ import annotations

import hmac
import math
import os
import re
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, render_template, request

from ..report import to_csv_text
from ..safety import UnsafeURL, check_public_url, normalise_start_url
from .jobs import Busy, JobManager


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _number(value: Any, label: str, cast: Callable[[Any], Any]):
    try:
        number = cast(value)
        if isinstance(number, float) and not math.isfinite(number):
            raise ValueError
        return number
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.")


def _error(message: str, status: int):
    return jsonify(error=message), status


def create_app(config: Optional[Dict[str, Any]] = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        MAX_PAGES_LIMIT=_env_int("EMAIL_SCANNER_MAX_PAGES", 300),
        MIN_DELAY=_env_float("EMAIL_SCANNER_MIN_DELAY", 0.2),
        MAX_CONCURRENT=_env_int("EMAIL_SCANNER_MAX_CONCURRENT", 2),
        MAX_SECONDS=_env_int("EMAIL_SCANNER_MAX_SECONDS", 600),
        ALLOW_PRIVATE=_env_flag("EMAIL_SCANNER_ALLOW_PRIVATE"),
        APP_PASSWORD=os.environ.get("EMAIL_SCANNER_PASSWORD", ""),
        MAX_CONTENT_LENGTH=10_000,
    )
    if config:
        app.config.update(config)

    guard = None if app.config["ALLOW_PRIVATE"] else check_public_url
    manager = JobManager(
        guard=guard,
        max_concurrent=app.config["MAX_CONCURRENT"],
        max_seconds=app.config["MAX_SECONDS"],
    )
    app.extensions["email_scanner_jobs"] = manager

    @app.before_request
    def require_password():
        password = app.config.get("APP_PASSWORD")
        if not password or request.path == "/healthz":
            return None
        auth = request.authorization
        supplied = (auth.password or "") if auth else ""
        if auth and hmac.compare_digest(supplied.encode(), password.encode()):
            return None
        return Response(
            "A password is needed to use this scanner.",
            401,
            {"WWW-Authenticate": 'Basic realm="Email Scanner"'},
        )

    @app.after_request
    def add_headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        limit = app.config["MAX_PAGES_LIMIT"]
        return render_template(
            "index.html", max_pages_limit=limit, default_pages=min(100, limit)
        )

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    @app.post("/api/scans")
    def start_scan():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("Send the request as JSON.", 400)
        try:
            url = normalise_start_url(str(data.get("url", "")))
            if guard is not None:
                guard(url)
            limit = app.config["MAX_PAGES_LIMIT"]
            max_pages = _number(data.get("max_pages", 100), "Pages", int)
            max_pages = max(1, min(max_pages, limit))
            delay = _number(data.get("delay", 0.5), "Speed", float)
            delay = max(app.config["MIN_DELAY"], min(delay, 10.0))
        except ValueError as exc:  # includes UnsafeURL
            return _error(str(exc), 400)

        try:
            job = manager.start(url, max_pages, delay)
        except Busy:
            return _error(
                "The scanner is busy with other scans. Try again in a minute.", 429
            )
        return jsonify(job.to_dict(include_results=False)), 202

    @app.get("/api/scans/<job_id>")
    def get_scan(job_id: str):
        job = manager.get(job_id)
        if job is None:
            return _error("That scan was not found. It may have expired.", 404)
        return jsonify(job.to_dict())

    @app.post("/api/scans/<job_id>/cancel")
    def cancel_scan(job_id: str):
        job = manager.cancel(job_id)
        if job is None:
            return _error("That scan was not found. It may have expired.", 404)
        return jsonify(job.to_dict(include_results=False))

    @app.get("/api/scans/<job_id>/csv")
    def download_csv(job_id: str):
        job = manager.get(job_id)
        if job is None:
            return _error("That scan was not found. It may have expired.", 404)
        host = (urlparse(job.url).hostname or "site").lower()
        host = re.sub(r"[^a-z0-9.\-]", "", host) or "site"
        return Response(
            to_csv_text(list(job.results)),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="emails_{host}.csv"'
            },
        )

    return app


def run() -> None:
    """Start the web app with a production grade server."""
    from waitress import serve

    host = os.environ.get("EMAIL_SCANNER_HOST", "127.0.0.1")
    port = _env_int("PORT", 8000)
    shown = "localhost" if host in ("0.0.0.0", "::") else host
    print(f"Email Scanner is running at http://{shown}:{port}  (press Ctrl+C to stop)")
    serve(create_app(), host=host, port=port, threads=8)
