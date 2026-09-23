#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PIN_FILE="$ROOT/.env"
RUNTIME_ENV="$ROOT/.env.runtime"
STATE_DIR="$HOME/.local/share/rss-to-kindle-deploy"
DEPLOYED_SHA_FILE="$HOME/.config/dagu/.rss-to-kindle-deployed-sha"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ -d "$ROOT/.git" ]] || fail "run this from a Git clone of rss-to-kindle"
[[ -f "$ROOT/docker-compose.yml" && -f "$ROOT/Dockerfile" ]] || fail "deployment files are missing from this checkout"
[[ -f "$RUNTIME_ENV" ]] || fail "copy the private runtime config to .env.runtime first"
[[ "$(stat -c '%a' "$RUNTIME_ENV" 2>/dev/null || stat -f '%Lp' "$RUNTIME_ENV")" == 600 ]] || \
  fail ".env.runtime must have mode 600"
for key in SENDER_EMAIL SENDER_PASSWORD KINDLE_EMAIL; do
  grep -q "^${key}=" "$RUNTIME_ENV" || fail ".env.runtime is missing $key"
done

mkdir -p "$ROOT/data" "$STATE_DIR" "$(dirname "$DEPLOYED_SHA_FILE")"
chmod 700 "$ROOT/data"
FS_TYPE=$(findmnt -n -o FSTYPE --target "$ROOT/data" 2>/dev/null || true)
[[ "$FS_TYPE" == ext4 ]] || fail "SQLite data must live on ext4 (found ${FS_TYPE:-unknown})"

SHA=$(git -C "$ROOT" rev-parse HEAD)
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || fail "could not read a Git commit SHA"
if [[ -e "$PIN_FILE" ]]; then
  IFS= read -r CURRENT_PIN < "$PIN_FILE" || true
  [[ "$CURRENT_PIN" == "IMAGE_TAG=$SHA" ]] || fail "existing .env pin does not match this checkout; inspect before changing it"
else
  (umask 077; printf 'IMAGE_TAG=%s\n' "$SHA" > "$PIN_FILE")
  chmod 600 "$PIN_FILE"
fi

cd "$ROOT"
IMAGE_TAG="$SHA" docker compose build --build-arg RUN_TESTS=1 web
IMAGE_TAG="$SHA" docker compose up -d --force-recreate web

HEALTHY=0
for _ in $(seq 1 18); do
  if curl -fsS --max-time 5 http://127.0.0.1:18788/api/health 2>/dev/null \
    | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    HEALTHY=1
    break
  fi
  sleep 5
done
[[ "$HEALTHY" == 1 ]] || fail "initial service health check failed; inspect docker compose logs web"

(umask 077; printf '%s\n' "$SHA" > "$DEPLOYED_SHA_FILE")
chmod 600 "$DEPLOYED_SHA_FILE"
"$ROOT/scripts/install-pi-deploy-controller.sh"
echo "Initial image $SHA is healthy. Apply the host integrations before installing the Dagu schedule."
