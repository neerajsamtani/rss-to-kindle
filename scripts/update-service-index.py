#!/usr/bin/env python3
"""Add the RSS-to-Kindle link to the existing Raspberry Pi service index."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = {
    "name": "RSS to Kindle",
    "description": "Queue an article and send it to your Kindle.",
    "url": "/rss-to-kindle/",
    "group": "Your library",
    "symbol": "K",
    "color": "orange",
    "tags": "rss articles read later kindle",
}


def update_service_index(services_path: Path) -> bool:
    text = services_path.read_text()
    services = json.loads(text)
    existing = next((service for service in services if service.get("name") == ENTRY["name"]), None)
    if existing is not None:
        if existing != ENTRY:
            raise ValueError("an RSS to Kindle card already exists with different settings")
        return False

    insert_before = next(
        (
            index
            for index, service in enumerate(services)
            if service.get("group") == "Manage your server"
        ),
        len(services),
    )
    services.insert(insert_before, ENTRY)

    lines = text.splitlines(keepends=True)
    anchor = next(
        (index for index, line in enumerate(lines) if '"group":"Manage your server"' in line),
        None,
    )
    if anchor is None and insert_before != len(services) - 1:
        raise ValueError("could not find the service-index group boundary")
    if anchor is None:
        anchor = len(lines) - 1

    entry_text = json.dumps(ENTRY, ensure_ascii=False, separators=(",", ":"))
    lines.insert(anchor, f"  {entry_text},\n")
    updated = "".join(lines)
    if json.loads(updated) != services:
        raise ValueError("service-index edit failed validation")

    backup = services_path.with_name(f"{services_path.name}.pre-rss-to-kindle")
    if not backup.exists():
        backup.write_text(text)
        backup.chmod(0o600)
    temporary = services_path.with_name(f"{services_path.name}.tmp.{os.getpid()}")
    temporary.write_text(updated)
    temporary.chmod(services_path.stat().st_mode & 0o777)
    temporary.replace(services_path)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "services_json",
        nargs="?",
        type=Path,
        default=ROOT.parent / "rasp" / "service-index" / "services.json",
        help="service-index/services.json (defaults to the sibling Pi reference checkout)",
    )
    parser.add_argument(
        "--no-build", action="store_true", help="edit source without rebuilding public/index.html"
    )
    args = parser.parse_args()
    try:
        changed = update_service_index(args.services_json)
        if not args.no_build:
            subprocess.run(
                [sys.executable, str(args.services_json.parent / "build.py")],
                check=True,
            )
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(
        "Added RSS to Kindle to the service index."
        if changed
        else "RSS to Kindle is already in the service index."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
