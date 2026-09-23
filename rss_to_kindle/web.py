"""Authenticated web interface for submitting articles to Kindle."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from .job_queue import IdempotencyConflict, JobStore, JobWorker
from .state import STATE_DIR


def _process_url(url: str, config: dict, on_stage=None):
    from .pipeline import send_url_to_kindle

    return send_url_to_kindle(url, config, on_stage=on_stage)


def _base_path(value: str) -> str:
    parts = [part for part in value.strip().split("/") if part]
    return f"/{'/'.join(parts)}" if parts else ""


def _same_origin() -> bool:
    origin = request.headers.get("Origin", "")
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return False
        expected_host = request.headers.get("X-Forwarded-Host", request.host).split(",", 1)[0]
        if parsed.netloc.casefold() != expected_host.strip().casefold():
            return False
        forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0]
        expected_scheme = forwarded_proto.strip().lower() or request.scheme
        return parsed.scheme == expected_scheme
    except ValueError:
        return False


def _db_path(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).expanduser()
    configured = os.getenv("WEB_DB_PATH", "").strip()
    return Path(configured).expanduser() if configured else STATE_DIR / "web-jobs.sqlite3"


def _load_delivery_config() -> dict:
    from .config import load_delivery_config

    return load_delivery_config()


def _drain_timeout() -> float:
    try:
        return max(1, min(600, float(os.getenv("WEB_DRAIN_TIMEOUT_SECONDS", "180"))))
    except ValueError:
        return 180


def create_app(
    *,
    delivery_config: dict | None = None,
    processor=None,
    database_path: str | Path | None = None,
    start_worker: bool = True,
    owner_login: str | None = None,
    base_path: str | None = None,
    drain_timeout: float | None = None,
) -> Flask:
    """Create the WSGI app, persistent job store, and optional single worker."""
    load_dotenv()
    app = Flask(__name__, static_folder="web_static", template_folder="web_templates")
    app.config["MAX_CONTENT_LENGTH"] = 8192
    app.config["WEB_BASE_PATH"] = _base_path(
        base_path if base_path is not None else os.getenv("WEB_BASE_PATH", "/rss-to-kindle")
    )
    raw_owner = owner_login if owner_login is not None else os.getenv("WEB_OWNER_LOGIN", "")
    app.config["WEB_OWNER_LOGINS"] = {
        value.strip().casefold() for value in raw_owner.split(",") if value.strip()
    }

    store = JobStore(_db_path(database_path))
    worker = None
    if start_worker:
        config = delivery_config if delivery_config is not None else _load_delivery_config()
        timeout = _drain_timeout() if drain_timeout is None else drain_timeout
        worker = JobWorker(store, config, processor or _process_url, drain_timeout=timeout)

    app.extensions["job_store"] = store
    app.extensions["job_worker"] = worker

    @app.before_request
    def authorize_request():
        if request.endpoint == "health" or request.endpoint == "static":
            return None
        allowed_logins = app.config["WEB_OWNER_LOGINS"]
        if not allowed_logins:
            return jsonify(error="Web owner access is not configured."), 503
        login = request.headers.get("Tailscale-User-Login", "").strip().casefold()
        if login not in allowed_logins:
            return jsonify(error="Forbidden."), 403
        if request.method == "POST" and not _same_origin():
            return jsonify(error="This request must come from the same site."), 403
        return None

    @app.after_request
    def add_security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "base-uri 'none'; object-src 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        return response

    @app.get("/")
    def index():
        focus_id = request.args.get("job", "")
        focused = store.get(focus_id) if focus_id else None
        if focus_id and focused is None:
            return jsonify(error="Job not found."), 404
        jobs = [_public_job(job, app.config["WEB_BASE_PATH"]) for job in store.recent(20)]
        return render_template(
            "index.html",
            base_path=app.config["WEB_BASE_PATH"],
            prefill=request.args.get("url", "")[:4096],
            jobs=jobs,
            current_job=(
                _public_job(focused, app.config["WEB_BASE_PATH"]) if focused is not None else None
            ),
        )

    @app.get("/jobs/<job_id>")
    def job_page(job_id: str):
        job = store.get(job_id)
        if job is None:
            return jsonify(error="Job not found."), 404
        return render_template(
            "index.html",
            base_path=app.config["WEB_BASE_PATH"],
            prefill="",
            jobs=[_public_job(record, app.config["WEB_BASE_PATH"]) for record in store.recent(20)],
            current_job=_public_job(job, app.config["WEB_BASE_PATH"]),
        )

    @app.get("/api/health")
    def health():
        database_ok = store.healthy()
        if worker is None:
            worker_state = "stopped"
        elif worker.is_stopping():
            worker_state = "draining" if worker.is_alive() else "dead"
        else:
            worker_state = "running" if worker.is_alive() else "dead"
        healthy = database_ok and worker_state == "running"
        return (
            jsonify(
                status="ok" if healthy else "unavailable",
                ok=healthy,
                database="ok" if database_ok else "unavailable",
                worker=worker_state,
            ),
            200 if healthy else 503,
        )

    @app.get("/api/jobs")
    def recent_jobs():
        limit = request.args.get("limit", default=20, type=int)
        if limit is None or not 1 <= limit <= 50:
            return jsonify(error="Limit must be between 1 and 50."), 400
        return jsonify(
            jobs=[_public_job(job, app.config["WEB_BASE_PATH"]) for job in store.recent(limit)]
        )

    @app.post("/api/jobs")
    def submit_job():
        if not request.is_json:
            return jsonify(error="Send a JSON body containing the article URL."), 415
        body = request.get_json(silent=True)
        url = body.get("url") if isinstance(body, dict) else None
        if not isinstance(url, str):
            return jsonify(error="Enter an article URL."), 400
        url = url.strip()
        try:
            parsed = urlsplit(url)
        except ValueError:
            parsed = None
        if (
            not url
            or len(url) > 4096
            or parsed is None
            or parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return jsonify(error="Enter a valid HTTP or HTTPS article URL."), 400

        if worker is None or not worker.is_alive():
            return jsonify(error="The article worker is unavailable."), 503
        if worker.is_stopping():
            return jsonify(error="The article worker is shutting down."), 503

        key = request.headers.get("Idempotency-Key", "")
        try:
            job, created = store.enqueue(url, key)
        except IdempotencyConflict as error:
            return jsonify(error=str(error)), 409
        except ValueError:
            return jsonify(error="A valid Idempotency-Key header is required."), 400

        worker.notify()
        public_job = _public_job(job, app.config["WEB_BASE_PATH"])
        response = jsonify(public_job)
        response.status_code = 202 if created else 200
        response.headers["Location"] = public_job["api_url"]
        return response

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id: str):
        job = store.get(job_id)
        if job is None:
            return jsonify(error="Job not found."), 404
        return jsonify(_public_job(job, app.config["WEB_BASE_PATH"]))

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return jsonify(error="Request body is too large."), 413

    return app


def _public_job(job: dict, base_path: str = "") -> dict:
    job_id = job["id"]
    return {
        "id": job_id,
        "url": job["url"],
        "status": job["status"],
        "title": job["title"],
        "error": job["error"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "status_url": f"{base_path}/jobs/{job_id}",
        "api_url": f"{base_path}/api/jobs/{job_id}",
    }
