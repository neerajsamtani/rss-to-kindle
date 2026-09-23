import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from rss_to_kindle.cli import main
from rss_to_kindle.extractor import LoginRequiredError
from rss_to_kindle.fetcher import UnsafeURL
from rss_to_kindle.job_queue import (
    DELIVERY_STATUS_UNSAVED,
    GENERIC_FAILURE,
    INTERRUPTED_BEFORE_SENDING,
    INTERRUPTED_DURING_SENDING,
    JobStore,
    JobWorker,
)
from rss_to_kindle.web import create_app


class WebServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.processed: list[str] = []

        def processor(url, _config, on_stage):
            self.processed.append(url)
            for stage in ("fetching", "extracting", "sending"):
                on_stage(stage)
            return SimpleNamespace(title="A readable article")

        self.app = create_app(
            delivery_config={},
            processor=processor,
            database_path=Path(self.temp.name) / "jobs.sqlite3",
            owner_login="owner@example.com",
            base_path="/rss-to-kindle",
            drain_timeout=5,
        )
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.store = self.app.extensions["job_store"]
        self.worker = self.app.extensions["job_worker"]

    def tearDown(self):
        self.worker.shutdown()
        self.temp.cleanup()

    def owner_headers(self, *, origin="http://localhost"):
        headers = {"Tailscale-User-Login": "OWNER@example.com"}
        if origin is not None:
            headers["Origin"] = origin
        return headers

    def wait_for_status(self, job_id, expected, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.store.get(job_id)
            if job["status"] == expected:
                return job
            time.sleep(0.02)
        self.fail(f"job {job_id} did not reach {expected!r}: {self.store.get(job_id)}")

    def submit(self, url="https://example.com/article", key="submission-key-00000001"):
        return self.client.post(
            "/api/jobs",
            json={"url": url},
            headers={**self.owner_headers(), "Idempotency-Key": key},
        )

    def test_owner_and_same_origin_protection(self):
        response = self.client.get("/", headers={"Tailscale-User-Login": "intruder@example.com"})
        self.assertEqual(response.status_code, 403)

        response = self.client.post(
            "/api/jobs",
            json={"url": "https://example.com/article"},
            headers={
                **self.owner_headers(origin="https://attacker.example"),
                "Idempotency-Key": "submission-key-00000001",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.store.recent(), [])

        response = self.client.post(
            "/api/jobs",
            json={"url": "https://example.com/article"},
            headers={
                **self.owner_headers(origin=None),
                "Idempotency-Key": "submission-key-00000001",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_health_is_private_detail_free_and_reports_worker(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"database": "ok", "ok": True, "status": "ok", "worker": "running"},
        )

    def test_static_assets_use_the_template_url_and_are_served(self):
        page = self.client.get("/", headers=self.owner_headers())
        html = page.get_data(as_text=True)
        self.assertIn("/rss-to-kindle/static/style.css", html)
        self.assertIn("/rss-to-kindle/static/app.js", html)
        page.close()

        script = self.client.get("/static/app.js")
        stylesheet = self.client.get("/static/style.css")
        self.assertEqual(script.status_code, 200)
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn("loadRecent()", script.get_data(as_text=True))
        self.assertIn(":root", stylesheet.get_data(as_text=True))
        script.close()
        stylesheet.close()

    def test_url_prefill_does_not_create_a_job(self):
        response = self.client.get(
            "/?url=https%3A%2F%2Fexample.com%2Farticle%3Fedition%3Dmorning",
            headers=self.owner_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'value="https://example.com/article?edition=morning"',
            response.get_data(as_text=True),
        )
        self.assertEqual(self.store.recent(), [])
        self.assertEqual(self.processed, [])

    def test_submission_is_idempotent_and_explicit_resubmission_is_new(self):
        first = self.submit()
        self.assertEqual(first.status_code, 202)
        first_job = first.get_json()
        self.assertTrue(first_job["status_url"].startswith("/rss-to-kindle/jobs/"))
        self.assertTrue(first_job["api_url"].startswith("/rss-to-kindle/api/jobs/"))
        self.assertEqual(first.headers["Location"], first_job["api_url"])
        self.wait_for_status(first_job["id"], "sent")

        retry = self.submit()
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.get_json()["id"], first_job["id"])

        conflict = self.submit("https://example.com/another", "submission-key-00000001")
        self.assertEqual(conflict.status_code, 409)

        explicit_resend = self.submit("https://example.com/article", "submission-key-00000002")
        self.assertEqual(explicit_resend.status_code, 202)
        second_id = explicit_resend.get_json()["id"]
        self.assertNotEqual(second_id, first_job["id"])
        self.wait_for_status(second_id, "sent")
        self.assertEqual(self.processed.count("https://example.com/article"), 2)

        page = self.client.get(f"/jobs/{first_job['id']}", headers=self.owner_headers())
        self.assertEqual(page.status_code, 200)
        self.assertIn("A readable article", page.get_data(as_text=True))

    def test_bad_url_and_bad_idempotency_key_are_rejected_before_enqueue(self):
        invalid_url = self.submit("file:///etc/passwd")
        self.assertEqual(invalid_url.status_code, 400)
        invalid_key = self.client.post(
            "/api/jobs",
            json={"url": "https://example.com/article"},
            headers={
                **self.owner_headers(),
                "Idempotency-Key": "short",
            },
        )
        self.assertEqual(invalid_key.status_code, 400)
        self.assertEqual(self.store.recent(), [])

    def test_restart_marks_active_jobs_interrupted_and_keeps_queued_jobs(self):
        statuses = {
            "fetching": "restart-key-fetching-0001",
            "extracting": "restart-key-extracting-01",
            "sending": "restart-key-sending-00001",
            "queued": "restart-key-queued-000001",
        }
        jobs = {}
        for status, key in statuses.items():
            job, _ = self.store.enqueue(f"https://example.com/{status}", key)
            jobs[status] = job["id"]
            if status != "queued":
                self.store.update(job["id"], status)

        self.store.mark_active_interrupted()
        self.assertEqual(self.store.get(jobs["queued"])["status"], "queued")
        fetching = self.store.get(jobs["fetching"])
        extracting = self.store.get(jobs["extracting"])
        sending = self.store.get(jobs["sending"])
        self.assertEqual(fetching["status"], "interrupted")
        self.assertEqual(fetching["error"], INTERRUPTED_BEFORE_SENDING)
        self.assertEqual(extracting["error"], INTERRUPTED_BEFORE_SENDING)
        self.assertEqual(sending["status"], "interrupted")
        self.assertEqual(sending["error"], INTERRUPTED_DURING_SENDING)

    def test_unexpected_pipeline_error_does_not_expose_exception_text(self):
        secret = "smtp-password-must-not-appear"

        def failing_processor(_url, _config, on_stage):
            on_stage("fetching")
            raise RuntimeError(secret)

        self.worker.processor = failing_processor
        response = self.submit()
        job = self.wait_for_status(response.get_json()["id"], "failed")
        self.assertEqual(job["error"], GENERIC_FAILURE)
        self.assertNotIn(secret, job["error"])

    def test_email_success_followed_by_db_error_is_not_reported_as_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:

            class StoreWithOneFailedSentWrite(JobStore):
                failed_once = False

                def update(self, job_id, status, **kwargs):
                    if status == "sent" and not self.failed_once:
                        self.failed_once = True
                        raise sqlite3.OperationalError("disk temporarily unavailable")
                    return super().update(job_id, status, **kwargs)

            store = StoreWithOneFailedSentWrite(Path(temp_dir) / "jobs.sqlite3")
            job, _ = store.enqueue("https://example.com/article", "post-send-key-000000001")

            def delivered(_url, _config, on_stage):
                on_stage("sending")
                return SimpleNamespace(title="Accepted")

            worker = JobWorker(store, {}, delivered, drain_timeout=5, install_signal_handler=False)
            self.addCleanup(worker.shutdown)
            worker.notify()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                result = store.get(job["id"])
                if result["status"] == "interrupted":
                    break
                time.sleep(0.02)
            self.assertEqual(result["status"], "interrupted")
            self.assertEqual(result["error"], DELIVERY_STATUS_UNSAVED)
            self.assertNotEqual(result["status"], "failed")
            worker.shutdown()

    def test_sigterm_drains_active_job_and_leaves_queued_work_for_restart(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "jobs.sqlite3"
            started = Path(temp_dir) / "processor-started"
            child_code = """
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from rss_to_kindle.job_queue import JobStore, JobWorker

database, started = Path(sys.argv[1]), Path(sys.argv[2])
store = JobStore(database)
store.enqueue("https://example.com/first", "sigterm-key-first-0000001")
store.enqueue("https://example.com/second", "sigterm-key-second-000001")

def processor(url, config, on_stage):
    started.write_text(url)
    time.sleep(0.4)
    return SimpleNamespace(title=url)

worker = JobWorker(store, {}, processor, drain_timeout=5)
worker.notify()
deadline = time.monotonic() + 4
while not started.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
if not started.exists():
    raise RuntimeError("worker did not start")
os.kill(os.getpid(), 15)
"""
            result = subprocess.run(
                [sys.executable, "-c", child_code, str(database), str(started)],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            store = JobStore(database)
            jobs = store.recent()
            statuses = sorted(job["status"] for job in jobs)
            self.assertEqual(statuses, ["queued", "sent"])
            sent_job = next(job for job in jobs if job["status"] == "sent")
            self.assertEqual(sent_job["url"], started.read_text())


class PreviewCliTests(unittest.TestCase):
    def test_substack_login_can_import_custom_domain_without_a_feed(self):
        runner = CliRunner()
        with patch("rss_to_kindle.cli._import_substack_cookie") as import_cookie:
            result = runner.invoke(
                main,
                [
                    "substack-login",
                    "--from-browser",
                    "chrome",
                    "--url",
                    "https://reader.example.com/publish",
                ],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        import_cookie.assert_called_once_with("chrome", False, "https://reader.example.com/publish")

    def test_locked_substack_preview_reports_safe_message_without_traceback(self):
        runner = CliRunner()
        with patch(
            "rss_to_kindle.pipeline.extract_url_article",
            side_effect=LoginRequiredError(False),
        ):
            result = runner.invoke(main, ["preview", "https://reader.example/article"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("This article requires Substack login", result.output)
        self.assertNotIn("Traceback", result.output)

    def test_unsafe_preview_url_reports_safe_message_without_traceback(self):
        runner = CliRunner()
        with patch(
            "rss_to_kindle.pipeline.extract_url_article",
            side_effect=UnsafeURL("Only public HTTP and HTTPS URLs are allowed."),
        ):
            result = runner.invoke(main, ["preview", "http://127.0.0.1/private"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Only public HTTP and HTTPS URLs are allowed.", result.output)
        self.assertNotIn("Traceback", result.output)


if __name__ == "__main__":
    unittest.main()
