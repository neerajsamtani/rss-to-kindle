import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/install-pi-host-integrations.py"
SPEC = importlib.util.spec_from_file_location("pi_host_integrations", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
INTEGRATIONS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INTEGRATIONS)


class PiHostIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.reconcile = """#!/usr/bin/env bash
set -uo pipefail
STACKS=(
  /home/neerajsamtani/servarr
  /home/neerajsamtani/hindi-music
)
for stack in "${STACKS[@]}"; do
  (cd "$stack" && docker compose up -d)
done
"""
        self.healthcheck = """ENDPOINTS=(
  "hindi-music|https://raspberrypi.taild579e4.ts.net/hindi-through-music/api/health|200"
  "service-index|https://raspberrypi.taild579e4.ts.net/|200"
)
"""
        self.backup = """sqlite3 "$DB" ".backup '$SNAPSHOT'"
sqlite3 "$SNAPSHOT" "PRAGMA integrity_check" | grep -qx ok
for DB in \\
        /var/lib/jellyfin/data/jellyfin.db ; do
  NAME=$(basename "$DB").snapshot
done
for PAIR in sonarr.db.snapshot:65536 radarr.db.snapshot:65536 \\
                    prowlarr.db.snapshot:65536 jellyfin.db.snapshot:65536 \\
                    immich-postgres.sql.gz:1048576 ; do
  true
done
tar -czf "$ARCHIVE" \\
        "home/neerajsamtani/service-index" \\
        "etc/jellyfin" \\
"""

    def test_adds_boot_recovery_stack_health_and_backup_entries(self):
        reconcile, health, backup = INTEGRATIONS.prepare(
            self.reconcile,
            self.healthcheck,
            self.backup,
        )

        self.assertIn("/home/neerajsamtani/rss-to-kindle", reconcile)
        self.assertLess(reconcile.index("set -uo pipefail"), reconcile.index("--recover-only"))
        self.assertLess(reconcile.index("--recover-only"), reconcile.index("docker compose up"))
        self.assertIn("--recover-only || exit 1", reconcile)
        self.assertIn("rss-to-kindle/api/health|200", health)
        self.assertIn("/home/neerajsamtani/rss-to-kindle/data/jobs.sqlite3", backup)
        self.assertIn("jobs.sqlite3.snapshot:512", backup)
        self.assertIn('"home/neerajsamtani/rss-to-kindle/.env.runtime"', backup)
        self.assertIn('"home/neerajsamtani/rss-to-kindle/docker-compose.yml"', backup)

    def test_repeated_application_is_idempotent(self):
        first = INTEGRATIONS.prepare(self.reconcile, self.healthcheck, self.backup)
        second = INTEGRATIONS.prepare(*first)
        self.assertEqual(first, second)

    def test_repairs_partial_host_edits_without_duplicate_entries(self):
        partially_updated = INTEGRATIONS.update_reconcile(self.reconcile)
        partially_updated = partially_updated.replace(
            "  /home/neerajsamtani/rss-to-kindle\n)",
            ")",
        )
        repaired = INTEGRATIONS.update_reconcile(partially_updated)
        self.assertEqual(repaired.count("  /home/neerajsamtani/rss-to-kindle\n"), 1)

        partial_backup = self.backup.replace(
            '        "home/neerajsamtani/service-index" \\\n',
            '        "home/neerajsamtani/service-index" \\\n'
            '        "home/neerajsamtani/rss-to-kindle/.env" \\\n',
        )
        repaired_backup = INTEGRATIONS.update_backup(partial_backup)
        self.assertEqual(repaired_backup.count('"home/neerajsamtani/rss-to-kindle/.env"'), 1)
        self.assertEqual(
            repaired_backup.count('"home/neerajsamtani/rss-to-kindle/.env.runtime"'), 1
        )

    def test_rejects_changed_host_anchors_without_partial_output(self):
        with self.assertRaisesRegex(ValueError, "last Compose stack"):
            INTEGRATIONS.update_reconcile("STACKS=(\n  /home/neerajsamtani/other\n)\n")

        with self.assertRaisesRegex(ValueError, "SQLite database list"):
            INTEGRATIONS.update_backup("sqlite3 .backup\nintegrity_check\n")


if __name__ == "__main__":
    unittest.main()
