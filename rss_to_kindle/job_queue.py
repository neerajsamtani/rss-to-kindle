"""Persistent SQLite queue for single-article web requests."""

import atexit
import logging
import re
import signal
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

JOB_STATUSES = frozenset(
    {"queued", "fetching", "extracting", "sending", "sent", "failed", "interrupted"}
)
INTERRUPTED_BEFORE_SENDING = (
    "The job stopped before email sending began. Submit the URL again if you still want it sent."
)
INTERRUPTED_DURING_SENDING = (
    "Delivery could already have occurred. Check your Kindle before submitting this URL again."
)
DELIVERY_STATUS_UNSAVED = (
    "The email may have been accepted, but the service could not save the final result. "
    "Check your Kindle before retrying."
)
GENERIC_FAILURE = "The article could not be processed because of an unexpected error."
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")


class IdempotencyConflict(ValueError):
    """Raised when a submission key is reused with a different URL."""


class JobStore:
    """SQLite-backed job history and queue operations."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_recent ON jobs(created_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_queued ON jobs(status, created_at)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

    def enqueue(self, url: str, idempotency_key: str) -> tuple[dict, bool]:
        """Persist a new job, or return the existing job for a retry."""
        if not IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
            raise ValueError("A valid Idempotency-Key header is required.")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                if existing["url"] != url:
                    raise IdempotencyConflict(
                        "This submission key was already used for a different URL."
                    )
                connection.commit()
                return dict(existing), False

            now = self._now()
            job_id = uuid.uuid4().hex
            connection.execute(
                """INSERT INTO jobs
                   (id, idempotency_key, url, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'queued', ?, ?)""",
                (job_id, idempotency_key, url, now, now),
            )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.commit()
            return dict(row), True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim_next(self) -> dict | None:
        """Atomically claim the oldest queued job as the sole worker."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            now = self._now()
            connection.execute(
                "UPDATE jobs SET status = 'fetching', error = NULL, updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            claimed = connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
            connection.commit()
            return dict(claimed)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update(
        self,
        job_id: str,
        status: str,
        *,
        title: str | None = None,
        error: str | None = None,
    ) -> None:
        if status not in JOB_STATUSES:
            raise ValueError(f"Unsupported job status: {status}")
        fields = ["status = ?", "updated_at = ?", "error = ?"]
        values: list[str | None] = [status, self._now(), error]
        if title is not None:
            fields.append("title = ?")
            values.append(title)
        values.append(job_id)
        with self._connection() as connection:
            connection.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)

    def mark_active_interrupted(self) -> None:
        with self._connection() as connection:
            for status, message in (
                ("fetching", INTERRUPTED_BEFORE_SENDING),
                ("extracting", INTERRUPTED_BEFORE_SENDING),
                ("sending", INTERRUPTED_DURING_SENDING),
            ):
                connection.execute(
                    "UPDATE jobs SET status = 'interrupted', error = ?, updated_at = ? "
                    "WHERE status = ?",
                    (message, self._now(), status),
                )

    def get(self, job_id: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row is not None else None

    def recent(self, limit: int = 20) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def healthy(self) -> bool:
        try:
            with self._connection() as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False


class JobWorker:
    """One background thread that drains queued jobs serially."""

    def __init__(
        self,
        store: JobStore,
        config: dict,
        processor: Callable,
        drain_timeout: float = 180,
        install_signal_handler: bool = True,
    ):
        self.store = store
        self.config = config
        self.processor = processor
        self.drain_timeout = max(1, drain_timeout)
        self._stopping = threading.Event()
        self._wake = threading.Event()
        self._claim_lock = threading.Lock()
        self._previous_sigterm = None
        if install_signal_handler and threading.current_thread() is threading.main_thread():
            self._previous_sigterm = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, self._handle_sigterm)
        self.thread = threading.Thread(
            target=self._run, name="rss-to-kindle-web-worker", daemon=True
        )
        self.store.mark_active_interrupted()
        self.thread.start()
        atexit.register(self.shutdown)

    def notify(self) -> None:
        self._wake.set()

    def is_alive(self) -> bool:
        return self.thread.is_alive()

    def is_stopping(self) -> bool:
        return self._stopping.is_set()

    def shutdown(self) -> None:
        """Finish the current job during graceful shutdown, without starting another."""
        with self._claim_lock:
            self._stopping.set()
        self._wake.set()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=self.drain_timeout)
        if (
            threading.current_thread() is threading.main_thread()
            and self._previous_sigterm is not None
            and signal.getsignal(signal.SIGTERM) == self._handle_sigterm
        ):
            signal.signal(signal.SIGTERM, self._previous_sigterm)
            self._previous_sigterm = None

    def _handle_sigterm(self, signum, frame) -> None:
        """Stop claiming immediately, then let the server's own TERM handler run."""
        with self._claim_lock:
            self._stopping.set()
        self._wake.set()
        previous = self._previous_sigterm
        if callable(previous):
            previous(signum, frame)
        elif previous != signal.SIG_IGN:
            self.shutdown()
            raise SystemExit(0)

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                with self._claim_lock:
                    if self._stopping.is_set():
                        return
                    job = self.store.claim_next()
            except Exception as error:
                logger.error("Web job queue became unavailable (%s).", type(error).__name__)
                return
            if job is None:
                self._wake.wait(timeout=1)
                self._wake.clear()
                continue

            try:
                article = self.processor(
                    job["url"],
                    self.config,
                    on_stage=lambda stage, job_id=job["id"]: self.store.update(job_id, stage),
                )
            except Exception as error:
                message = self._public_error(error)
                try:
                    self.store.update(job["id"], "failed", error=message)
                except Exception as update_error:
                    logger.error("Could not save web job result (%s).", type(update_error).__name__)
                if message == GENERIC_FAILURE:
                    logger.error(
                        "Web job %s failed with an unexpected error (%s).",
                        job["id"],
                        type(error).__name__,
                    )
                continue

            try:
                self.store.update(job["id"], "sent", title=getattr(article, "title", None))
            except Exception as error:
                logger.error(
                    "Email was accepted for web job %s, but its final status could not be saved (%s).",
                    job["id"],
                    type(error).__name__,
                )
                try:
                    self.store.update(job["id"], "interrupted", error=DELIVERY_STATUS_UNSAVED)
                except Exception as update_error:
                    logger.error(
                        "Could not save the uncertain delivery state (%s).",
                        type(update_error).__name__,
                    )

    @staticmethod
    def _public_error(error: Exception) -> str:
        try:
            from .pipeline import PipelineError

            if isinstance(error, PipelineError) and isinstance(error.user_message, str):
                return error.user_message
        except (ImportError, AttributeError):
            pass
        return GENERIC_FAILURE
