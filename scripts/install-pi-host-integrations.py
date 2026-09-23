#!/usr/bin/env python3
"""Safely add rss-to-kindle to existing Pi boot, health, and backup config."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

HOME = Path.home()
REPO = HOME / "rss-to-kindle"
CONTROLLER = HOME / ".local/bin/rss-to-kindle-deploy-controller"
BACKUP_DIR = HOME / ".local/share/rss-to-kindle-deploy/host-backups"
RECONCILE = Path("/usr/local/sbin/compose-reconcile.sh")
HEALTHCHECK = Path("/usr/local/sbin/compose-healthcheck.sh")
BACKUP_DAG = HOME / ".config/dagu/dags/config-backup.yaml"
STACK_LINE = "/home/neerajsamtani/rss-to-kindle"
HEALTH_LINE = '  "rss-to-kindle|https://raspberrypi.taild579e4.ts.net/rss-to-kindle/api/health|200"'


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected one {label} anchor, found {count}")
    return text.replace(old, new, 1)


def update_reconcile(text: str) -> str:
    if f"  {STACK_LINE}\n" not in text:
        old = "  /home/neerajsamtani/hindi-music\n)"
        new = f"  /home/neerajsamtani/hindi-music\n  {STACK_LINE}\n)"
        text = _replace_once(text, old, new, "last Compose stack")

    recovery = f'''# Recover an interrupted RSS-to-Kindle deploy before plain Compose consumes its image pin.
if [[ -d "{REPO}/.git" ]]; then
  [[ -x "{CONTROLLER}" ]] || {{ echo "RSS-to-Kindle deploy controller is missing" >&2; exit 1; }}
  "{CONTROLLER}" --recover-only || exit 1
fi

'''
    if recovery not in text:
        marker = "set -uo pipefail\n"
        count = text.count(marker)
        if count != 1:
            raise ValueError(f"expected one shell strict-mode anchor, found {count}")
        text = text.replace(marker, marker + recovery, 1)
    return text


def update_healthcheck(text: str) -> str:
    if HEALTH_LINE in text:
        return text
    anchor = '  "service-index|https://raspberrypi.taild579e4.ts.net/|200"'
    return _replace_once(text, anchor, anchor + "\n" + HEALTH_LINE, "service-index health probe")


def update_backup(text: str) -> str:
    if "/home/neerajsamtani/rss-to-kindle/data/jobs.sqlite3" not in text:
        anchor = "        /var/lib/jellyfin/data/jellyfin.db ; do"
        updated = (
            "        /var/lib/jellyfin/data/jellyfin.db \\\n"
            "        /home/neerajsamtani/rss-to-kindle/data/jobs.sqlite3 ; do"
        )
        text = _replace_once(text, anchor, updated, "SQLite database list")

    if "jobs.sqlite3.snapshot:512" not in text:
        anchor = "immich-postgres.sql.gz:1048576 ; do"
        updated = "jobs.sqlite3.snapshot:512 \\\n                    " + anchor
        text = _replace_once(text, anchor, updated, "backup size verification list")

    archive_anchor = '        "home/neerajsamtani/service-index" \\\n'
    archive_paths = (
        '        "home/neerajsamtani/rss-to-kindle/.env" \\\n',
        '        "home/neerajsamtani/rss-to-kindle/.env.runtime" \\\n',
        '        "home/neerajsamtani/rss-to-kindle/docker-compose.yml" \\\n',
    )
    missing_archive_paths = [path for path in archive_paths if path not in text]
    if missing_archive_paths:
        text = _replace_once(
            text,
            archive_anchor,
            archive_anchor + "".join(missing_archive_paths),
            "archive path list",
        )

    # The archive carries config and the SQLite-consistent snapshot, not an
    # in-use database/WAL copied from the ext4 data directory.
    if "jobs.sqlite3.snapshot" not in text:
        raise ValueError("SQLite snapshot was not added to backup verification")
    if ".backup" not in text or "integrity_check" not in text:
        raise ValueError("existing backup flow must use SQLite backup and integrity checks")
    return text


def _sudo_read(path: Path) -> str:
    return subprocess.check_output(["sudo", "cat", str(path)], text=True)


def _validate_shell(text: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as handle:
        handle.write(text)
        staged = Path(handle.name)
    try:
        subprocess.run(["bash", "-n", str(staged)], check=True)
    finally:
        staged.unlink(missing_ok=True)


def _validate_dagu(text: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".yaml", delete=False
    ) as handle:
        handle.write(text)
        staged = Path(handle.name)
    try:
        subprocess.run([str(HOME / ".local/bin/dagu"), "validate", str(staged)], check=True)
    finally:
        staged.unlink(missing_ok=True)


def _install_root_file(path: Path, text: str, backup: Path) -> None:
    if not backup.exists():
        subprocess.run(["sudo", "cp", "-p", str(path), str(backup)], check=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as handle:
        handle.write(text)
        staged = Path(handle.name)
    try:
        subprocess.run(["bash", "-n", str(staged)], check=True)
        subprocess.run(["sudo", "install", "-m", "0755", str(staged), str(path)], check=True)
    finally:
        staged.unlink(missing_ok=True)


def _install_user_file(path: Path, text: str, backup: Path) -> None:
    if not backup.exists():
        shutil.copy2(path, backup)
        backup.chmod(0o600)
    staged = path.with_name(f".{path.name}.rss-to-kindle.tmp")
    staged.write_text(text)
    staged.chmod(path.stat().st_mode & 0o777)
    subprocess.run([str(HOME / ".local/bin/dagu"), "validate", str(staged)], check=True)
    staged.replace(path)


def prepare(reconcile: str, healthcheck: str, backup: str) -> tuple[str, str, str]:
    return update_reconcile(reconcile), update_healthcheck(healthcheck), update_backup(backup)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="install changes on this Pi")
    args = parser.parse_args()
    try:
        subprocess.run(["sudo", "-n", "true"], check=True)
        prepared = prepare(
            _sudo_read(RECONCILE),
            _sudo_read(HEALTHCHECK),
            BACKUP_DAG.read_text(),
        )
        if not args.apply:
            print(
                "Pi host integration checks passed. Re-run with --apply to install the prepared changes."
            )
            return 0

        _validate_shell(prepared[0])
        _validate_shell(prepared[1])
        _validate_dagu(prepared[2])
        BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        BACKUP_DIR.chmod(0o700)
        _install_root_file(RECONCILE, prepared[0], BACKUP_DIR / RECONCILE.name)
        _install_root_file(HEALTHCHECK, prepared[1], BACKUP_DIR / HEALTHCHECK.name)
        _install_user_file(BACKUP_DAG, prepared[2], BACKUP_DIR / BACKUP_DAG.name)

        subprocess.run(
            [
                "python3",
                str(REPO / "scripts/update-service-index.py"),
                str(HOME / "service-index/services.json"),
            ],
            check=True,
        )
        subprocess.run(
            [str(REPO / "scripts/install-pi-dagu-deploy.sh")],
            check=True,
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(f"Installed Pi host integration. Original config backups are in {BACKUP_DIR}.")
    print("Add only the /rss-to-kindle/ Tailscale Serve path as documented in README.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
