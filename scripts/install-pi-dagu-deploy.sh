#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DAG_DIR="$HOME/.config/dagu/dags"
DAG_FILE="$DAG_DIR/rss-to-kindle-deploy.yaml"
SOURCE="$ROOT/deploy/pi/rss-to-kindle-deploy.yaml"

[[ -x "$HOME/.local/bin/rss-to-kindle-deploy-controller" ]] || {
  echo "Stable deploy controller is not installed yet." >&2
  exit 1
}
[[ -f "$HOME/.config/dagu/.rss-to-kindle-deployed-sha" ]] || {
  echo "Initial stack has not been bootstrapped yet." >&2
  exit 1
}
install -d -m 700 "$DAG_DIR"
if [[ -e "$DAG_FILE" ]] && ! cmp -s "$SOURCE" "$DAG_FILE"; then
  echo "Refusing to overwrite an existing DAG: $DAG_FILE" >&2
  exit 1
fi
install -m 600 "$SOURCE" "$DAG_FILE"
"$HOME/.local/bin/dagu" validate "$DAG_FILE"
echo "Installed $DAG_FILE. Dagu will poll public origin/main every five minutes."
