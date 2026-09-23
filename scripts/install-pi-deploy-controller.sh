#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TARGET=${RSS_TO_KINDLE_CONTROLLER_PATH:-"$HOME/.local/bin/rss-to-kindle-deploy-controller"}

if [[ -e "$TARGET" ]]; then
  echo "Refusing to overwrite the existing stable controller: $TARGET" >&2
  exit 1
fi

install -d -m 700 "$(dirname "$TARGET")"
install -m 755 "$ROOT/scripts/pi-deploy-controller.sh" "$TARGET"
echo "Installed stable deploy controller at $TARGET"
