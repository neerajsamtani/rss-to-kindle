#!/usr/bin/env bash
# Stable Pi-side poller. Install outside the checkout so a bad commit cannot
# replace the code responsible for restoring the last known-good release.
set -Eeuo pipefail

REPO_DIR=${RSS_TO_KINDLE_REPO_DIR:-"$HOME/rss-to-kindle"}
STATE_DIR=${RSS_TO_KINDLE_DEPLOY_STATE_DIR:-"$HOME/.local/share/rss-to-kindle-deploy"}
FAILED_SHA_FILE=${RSS_TO_KINDLE_FAILED_SHA_FILE:-"$HOME/.config/dagu/.rss-to-kindle-deploy-failed-sha"}
DEPLOYED_SHA_FILE=${RSS_TO_KINDLE_DEPLOYED_SHA_FILE:-"$HOME/.config/dagu/.rss-to-kindle-deployed-sha"}
LOCK_FILE=${RSS_TO_KINDLE_LOCK_FILE:-"$HOME/.config/dagu/.rss-to-kindle-deploy.lock"}
HEALTH_URL=${RSS_TO_KINDLE_HEALTH_URL:-http://127.0.0.1:18788/api/health}
LOG_FILE=${RSS_TO_KINDLE_LOG_FILE:-/tmp/rss-to-kindle-deploy.log}
SERVICE=${RSS_TO_KINDLE_SERVICE_NAME:-web}
IMAGE_REPO=${RSS_TO_KINDLE_IMAGE_REPO:-rss-to-kindle}
PIN_FILE="$REPO_DIR/.env"
RUNTIME_ENV="$REPO_DIR/.env.runtime"
COMPOSE_FILE="$REPO_DIR/docker-compose.yml"
TRANSACTION_FILE="$STATE_DIR/transaction"
COMPOSE_BACKUP="$STATE_DIR/docker-compose.yml.previous"
GIT_BIN=${GIT_BIN:-git}
REQUIRE_EXT4=${RSS_TO_KINDLE_REQUIRE_EXT4:-1}
LOCK_HELD=0

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

die() {
  log "ERROR: $*" >&2
  exit 1
}

sha_is_valid() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]]
}

file_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}

write_atomic() {
  local target=$1
  local contents=$2
  local temporary="${target}.tmp.$$"
  (umask 077; printf '%s\n' "$contents" > "$temporary")
  chmod 600 "$temporary"
  mv -f "$temporary" "$target"
}

read_image_pin() {
  local line
  [[ -f "$PIN_FILE" ]] || die "missing $PIN_FILE; initialize the stack before enabling Dagu"
  [[ "$(file_mode "$PIN_FILE")" == 600 ]] || die "$PIN_FILE must have mode 600"
  IFS= read -r line < "$PIN_FILE" || true
  [[ "$line" =~ ^IMAGE_TAG=([0-9a-f]{40})$ ]] || die "$PIN_FILE must contain only IMAGE_TAG=<40-character commit SHA>"
  IMAGE_PIN=${BASH_REMATCH[1]}
  [[ $(wc -l < "$PIN_FILE" | tr -d ' ') == 1 ]] || die "$PIN_FILE must contain exactly one line"
}

read_marker() {
  local path=$1
  if [[ -f "$path" ]]; then
    IFS= read -r MARKER_VALUE < "$path" || true
  else
    MARKER_VALUE=
  fi
}

compose() {
  local image_tag=$1
  shift
  (cd "$REPO_DIR" && IMAGE_TAG="$image_tag" docker compose "$@")
}

notify() {
  local title=$1
  local body=$2
  local tags=${3:-rotating_light}
  [[ -n "${NTFY_TOPIC:-}" ]] || return 0
  curl -sS --max-time 10 -o /dev/null \
    -H "Title: $title" -H "Tags: $tags" \
    --data-binary "$body" "https://ntfy.sh/${NTFY_TOPIC}" >/dev/null 2>&1 || true
}

record_failed_sha() {
  write_atomic "$FAILED_SHA_FILE" "$1"
}

git_reset_clean() {
  local target=$1
  if ! "$GIT_BIN" -C "$REPO_DIR" diff --quiet HEAD --; then
    log "Tracked files changed during deployment; refusing to overwrite local work. Inspect $REPO_DIR manually."
    return 1
  fi
  "$GIT_BIN" -C "$REPO_DIR" reset --hard "$target" >/dev/null
}

recover_transaction() {
  local old_sha candidate_sha pinned deployed old_line candidate_line extra_line
  [[ -f "$TRANSACTION_FILE" ]] || return 0
  [[ -f "$COMPOSE_BACKUP" ]] || die "incomplete deploy transaction: missing Compose snapshot at $COMPOSE_BACKUP"
  {
    IFS= read -r old_line
    IFS= read -r candidate_line
    extra_line=
    IFS= read -r extra_line || true
  } < "$TRANSACTION_FILE"
  [[ "$old_line" =~ ^OLD_SHA=([0-9a-f]{40})$ ]] || die "invalid prior SHA in $TRANSACTION_FILE"
  old_sha=${BASH_REMATCH[1]}
  [[ "$candidate_line" =~ ^CANDIDATE_SHA=([0-9a-f]{40})$ ]] || die "invalid candidate SHA in $TRANSACTION_FILE"
  [[ -z "${extra_line:-}" ]] || die "unexpected data in $TRANSACTION_FILE"
  candidate_sha=${candidate_line#CANDIDATE_SHA=}
  sha_is_valid "$old_sha" && sha_is_valid "$candidate_sha" || die "invalid deploy transaction state; inspect $TRANSACTION_FILE"

  read_image_pin
  pinned=$IMAGE_PIN
  read_marker "$DEPLOYED_SHA_FILE"
  deployed=$MARKER_VALUE

  if [[ "$pinned" == "$candidate_sha" && "$deployed" == "$candidate_sha" ]]; then
    rm -f "$TRANSACTION_FILE" "$COMPOSE_BACKUP"
    log "Completed cleanup for successful deployment $candidate_sha"
    return 0
  fi

  log "Recovering interrupted deployment $candidate_sha; restoring $old_sha"
  git_reset_clean "$old_sha" || die "cannot safely restore interrupted deployment; tracked working tree needs review"
  cp -p "$COMPOSE_BACKUP" "$COMPOSE_FILE"
  write_atomic "$PIN_FILE" "IMAGE_TAG=$old_sha"
  write_atomic "$DEPLOYED_SHA_FILE" "$old_sha"
  record_failed_sha "$candidate_sha"

  if ! compose "$old_sha" up -d "$SERVICE"; then
    notify "RSS to Kindle rollback needs attention" \
      "Interrupted deploy $candidate_sha was reverted to $old_sha, but Compose could not start the previous service. Check the Pi deployment log." \
      "rotating_light"
    die "rollback Compose failed; transaction retained for the next recovery attempt"
  fi

  rm -f "$TRANSACTION_FILE" "$COMPOSE_BACKUP"
  notify "RSS to Kindle deploy interrupted" \
    "Interrupted deploy $candidate_sha was restored to $old_sha. The failed SHA is held until main moves or the marker is cleared." \
    "warning"
  log "Restored the previous image and Compose file at $old_sha"
}

check_preconditions() {
  local dependency mode fs
  for dependency in "$GIT_BIN" docker curl flock; do
    command -v "$dependency" >/dev/null 2>&1 || die "required command is missing: $dependency"
  done
  [[ -d "$REPO_DIR/.git" ]] || die "expected a Git clone at $REPO_DIR"
  [[ -f "$COMPOSE_FILE" ]] || die "missing $COMPOSE_FILE"
  [[ -f "$RUNTIME_ENV" ]] || die "missing $RUNTIME_ENV; install runtime secrets with mode 600"
  mode=$(file_mode "$RUNTIME_ENV")
  [[ "$mode" == 600 ]] || die "$RUNTIME_ENV must have mode 600 (found $mode)"
  read_image_pin

  if [[ "$REQUIRE_EXT4" == 1 ]]; then
    command -v findmnt >/dev/null 2>&1 || die "findmnt is required to verify the SQLite volume filesystem"
    fs=$(findmnt -n -o FSTYPE --target "$REPO_DIR/data" 2>/dev/null || true)
    [[ "$fs" == ext4 ]] || die "SQLite volume $REPO_DIR/data must be on ext4 (found ${fs:-unknown})"
  fi
}

check_recovery_preconditions() {
  local dependency
  for dependency in "$GIT_BIN" docker flock; do
    command -v "$dependency" >/dev/null 2>&1 || die "required recovery command is missing: $dependency"
  done
  [[ -d "$REPO_DIR/.git" ]] || die "expected a Git clone at $REPO_DIR for transaction recovery"
  [[ -f "$COMPOSE_BACKUP" ]] || die "missing prior Compose snapshot at $COMPOSE_BACKUP"
  read_image_pin
}

check_untracked_collisions() {
  local path tracked
  while IFS= read -r -d '' path; do
    path=${path%/}
    [[ -n "$path" ]] || continue
    tracked=$("$GIT_BIN" -C "$REPO_DIR" ls-tree -r --name-only "$1" -- "$path")
    if [[ -n "$tracked" ]]; then
      die "untracked path '$path' collides with the candidate commit; preserving it and skipping deploy"
    fi
  done < <("$GIT_BIN" -C "$REPO_DIR" ls-files --others --directory -z)
}

deploy_main() {
  local local_sha remote_sha failed_sha build_log attempt healthy
  check_preconditions

  read_image_pin
  local_sha=$IMAGE_PIN
  [[ "$("$GIT_BIN" -C "$REPO_DIR" rev-parse HEAD)" == "$local_sha" ]] || \
    die "checkout HEAD does not match last successful image $local_sha; inspect the checkout before deploying"
  "$GIT_BIN" -C "$REPO_DIR" diff --quiet HEAD -- || die "tracked checkout has local edits; refusing to deploy"
  "$GIT_BIN" -C "$REPO_DIR" fetch --quiet origin main || die "git fetch origin main failed"
  remote_sha=$("$GIT_BIN" -C "$REPO_DIR" rev-parse --verify 'origin/main^{commit}')

  read_marker "$FAILED_SHA_FILE"
  failed_sha=$MARKER_VALUE
  if [[ "$remote_sha" == "$local_sha" ]]; then
    log "Already serving main at ${local_sha:0:12}"
    return 0
  fi
  if [[ "$remote_sha" == "$failed_sha" ]]; then
    log "Commit ${remote_sha:0:12} already failed; waiting for a new commit or manual marker clear"
    return 0
  fi

  check_untracked_collisions "$remote_sha"
  if ! docker image inspect "${IMAGE_REPO}:$local_sha" >/dev/null 2>&1; then
    die "previous image ${IMAGE_REPO}:$local_sha is missing; refusing a deployment without rollback image"
  fi

  mkdir -p "$STATE_DIR" "$(dirname "$FAILED_SHA_FILE")" "$(dirname "$DEPLOYED_SHA_FILE")"
  cp -p "$COMPOSE_FILE" "$COMPOSE_BACKUP"
  chmod 600 "$COMPOSE_BACKUP"
  write_atomic "$TRANSACTION_FILE" "OLD_SHA=$local_sha
CANDIDATE_SHA=$remote_sha"
  log "Building ${remote_sha:0:12} with the test gate; ${local_sha:0:12} remains live"
  : > "$LOG_FILE"

  git_reset_clean "$remote_sha" || die "could not move the clean checkout to the candidate commit"
  if ! (cd "$REPO_DIR" && IMAGE_TAG="$remote_sha" docker compose build --build-arg RUN_TESTS=1 "$SERVICE") >>"$LOG_FILE" 2>&1; then
    record_failed_sha "$remote_sha"
    notify "RSS to Kindle build failed" \
      "Commit ${remote_sha:0:12} failed its Docker build or test gate. ${local_sha:0:12} remains live." \
      "rotating_light"
    die "candidate build or tests failed; previous release remains live"
  fi

  docker image tag "${IMAGE_REPO}:$local_sha" "${IMAGE_REPO}:previous" || \
    die "could not preserve the previous image tag"
  if ! compose "$local_sha" stop --timeout 240 "$SERVICE"; then
    record_failed_sha "$remote_sha"
    die "graceful stop failed; restoring the previous release"
  fi
  if ! compose "$remote_sha" up -d --no-deps --force-recreate "$SERVICE"; then
    record_failed_sha "$remote_sha"
    die "candidate Compose start failed; restoring the previous release"
  fi

  healthy=0
  for ((attempt = 1; attempt <= 18; attempt++)); do
    if curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null \
      | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
      healthy=1
      break
    fi
    sleep 5
  done
  if [[ "$healthy" != 1 ]]; then
    (cd "$REPO_DIR" && docker compose logs --tail 30 "$SERVICE") >>"$LOG_FILE" 2>&1 || true
    record_failed_sha "$remote_sha"
    die "candidate did not pass health checks within 90 seconds; restoring the previous release"
  fi

  write_atomic "$PIN_FILE" "IMAGE_TAG=$remote_sha"
  write_atomic "$DEPLOYED_SHA_FILE" "$remote_sha"
  rm -f "$FAILED_SHA_FILE"
  rm -f "$TRANSACTION_FILE" "$COMPOSE_BACKUP"
  notify "RSS to Kindle deployed ${remote_sha:0:12}" \
    "The tested main commit is healthy at http://127.0.0.1:18788. The service index route is /rss-to-kindle/." \
    "rocket"
  log "Successfully deployed ${remote_sha:0:12}"
}

main() {
  local mode=${1:-deploy}
  [[ $# -le 1 ]] || die "usage: $0 [--recover-only]"
  mkdir -p "$STATE_DIR" "$(dirname "$LOCK_FILE")"
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    if [[ "$mode" == --recover-only ]]; then
      die "deployment lock is held; boot recovery must finish before Compose reconciliation"
    fi
    log "Another deployment holds the lock; skipping this poll"
    return 0
  fi
  LOCK_HELD=1

  if [[ -f "$TRANSACTION_FILE" ]]; then
    check_recovery_preconditions
    recover_transaction
  elif [[ -f "$COMPOSE_BACKUP" ]]; then
    rm -f "$COMPOSE_BACKUP"
  fi
  if [[ "$mode" == --recover-only ]]; then
    log "No interrupted deployment needs recovery"
    return 0
  fi
  [[ "$mode" == deploy ]] || die "usage: $0 [--recover-only]"
  deploy_main
}

on_exit() {
  local exit_code=$?
  trap - EXIT
  if [[ "$LOCK_HELD" == 1 && -f "$TRANSACTION_FILE" ]]; then
    if ! recover_transaction; then
      log "Automatic rollback did not finish; transaction state is retained at $TRANSACTION_FILE"
      exit_code=1
    fi
  fi
  exit "$exit_code"
}

trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' HUP TERM

main "$@"
