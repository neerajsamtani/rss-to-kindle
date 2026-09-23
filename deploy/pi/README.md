# Raspberry Pi deployment

The Pi runs the ARM64 Python 3.12 container with one synchronous Gunicorn worker and an in-process
SQLite job worker. Keep `~/rss-to-kindle/data/jobs.sqlite3` on the Pi's ext4 root volume; `/mnt/sda1`
is an exFAT backup drive. The web container binds only to `127.0.0.1:18788`; Tailscale Serve owns
the `/rss-to-kindle/` path on HTTPS 443. The owner allowlist is
`neerajjsamtani@gmail.com`.

## First install

Clone the tracked application on the Pi. Do not rsync application source or use a Mac-built image:

```sh
ssh neerajsamtani@raspberrypi
git clone https://github.com/neerajsamtani/rss-to-kindle.git ~/rss-to-kindle
```

From the Mac, transfer the existing private settings file as a runtime-only file. It is ignored by
Git and must stay mode `600`; do not print it in a terminal or paste its contents into chat:

```sh
chmod 600 .env
rsync -av .env neerajsamtani@raspberrypi:~/rss-to-kindle/.env.runtime
ssh neerajsamtani@raspberrypi 'chmod 600 "$HOME/rss-to-kindle/.env.runtime"'
```

On the Pi, verify the secret file and data filesystem, then build and start the initial tested
image. `FEEDS` is optional; `SENDER_EMAIL`, `SENDER_PASSWORD`, and `KINDLE_EMAIL` are required.

```sh
cd ~/rss-to-kindle
chmod 600 .env.runtime
sudo -n true
./scripts/bootstrap-pi-stack.sh
```

Bootstrap checks the existing image pin, builds with the Docker test gate enabled, waits for
`/api/health`, and installs the stable deployment controller under `~/.local/bin`, outside the
Git checkout.

After the initial health check, install boot, health-probe, backup, service-index, and Dagu
integration. This backs up the original Pi configuration under
`~/.local/share/rss-to-kindle-deploy/host-backups/` before changing it:

```sh
python3 scripts/install-pi-host-integrations.py --apply
```

The integration adds `~/rss-to-kindle` to boot reconciliation and runs the stable controller's
`--recover-only` mode before Compose can consume an interrupted image pin. It adds the external
health probe, a SQLite `.backup` snapshot with `integrity_check` and size validation, and archives
`.env`, `.env.runtime`, and `docker-compose.yml`. The application database is not copied live;
source code remains available from Git. It adds the app card to `~/service-index` and installs
the five-minute Dagu deploy job with `CRON_TZ=America/New_York`.

Add the one path-scoped Tailscale route after checking the existing Serve configuration:

```sh
sudo tailscale serve status
sudo tailscale serve --bg --https=443 --set-path=/rss-to-kindle/ http://127.0.0.1:18788
sudo tailscale serve status
```

Confirm the root service index, `/hindi-through-music/`, existing 2283 and 5055 routes, and the
8443 OPDS Funnel are still present, along with `/rss-to-kindle/`. This route is tailnet-only.
Never run `tailscale serve reset`, remove another path, or enable Funnel on 443. The service index
link is at `https://raspberrypi.taild579e4.ts.net/`.

Check the local and external health endpoints and deployed image:

```sh
curl -fsS http://127.0.0.1:18788/api/health
curl -fsS https://raspberrypi.taild579e4.ts.net/rss-to-kindle/api/health
docker compose ps
```

The health endpoint returns overall, database, and worker status plus an `ok` flag; it exposes no
delivery settings. Tailscale must be connected on the client device before opening
`https://raspberrypi.taild579e4.ts.net/rss-to-kindle/`.

## Routine updates and service checks

Push reviewed application changes to `main`; Dagu checks public `origin/main` every five minutes.
It builds and tests the candidate image before stopping the live service. Do not rsync source code
or build an image on the Mac. To request an immediate poll:

```sh
ssh neerajsamtani@raspberrypi '$HOME/.local/bin/rss-to-kindle-deploy-controller'
```

Connect to the Pi, then check the container, direct health endpoint, deployment history, and logs:

```sh
ssh neerajsamtani@raspberrypi
```

```sh
cd ~/rss-to-kindle
docker compose ps
curl -fsS http://127.0.0.1:18788/api/health
~/.local/bin/dagu history rss-to-kindle-deploy --last 24h
docker compose logs --tail 100 web
tail -n 100 /tmp/rss-to-kindle-deploy.log
```

The Dagu history shows whether a poll succeeded. The controller log contains candidate build and
test output; Compose logs show app startup and worker errors. The health endpoint should report
`status: ok`, `database: ok`, and `worker: running`. To check an individual request, open
`https://raspberrypi.taild579e4.ts.net/rss-to-kindle/jobs/<job-id>` while connected to Tailscale.

## Failed deploys and rollback

The Compose `.env` contains only the last healthy `IMAGE_TAG`. The controller builds while the
old image remains live, then stops the app gracefully and starts the candidate. A build, startup,
or health failure restores the prior image pin and saved Compose file immediately. It records the
failed SHA at `~/.config/dagu/.rss-to-kindle-deploy-failed-sha` and skips that same SHA on later
polls. Inspect the controller log and Compose logs, fix the cause in Git, and push a new commit;
Dagu will deploy it. If you need to undo a release that passed health checks, push a revert commit
to `main` and let the normal tested path deploy it. Do not edit `.env` or change image tags by
hand.

Only clear the failed-SHA marker to retry that exact commit after confirming the failure cause is
fixed or transient:

```sh
rm ~/.config/dagu/.rss-to-kindle-deploy-failed-sha
~/.local/bin/rss-to-kindle-deploy-controller
```

The controller refuses dirty tracked files and candidate commits that would overwrite untracked
Pi files. Never run `git clean` or discard local files to force a deployment. If power fails mid-
deploy, boot reconciliation runs the stable controller in `--recover-only` mode before Compose
starts; leave its transaction and Compose snapshot in
`~/.local/share/rss-to-kindle-deploy/` intact until recovery succeeds.

## Backups and restoring the job database

The `config-backup` Dagu job runs nightly at 2:17 a.m. New York time. Check its recent runs with
`~/.local/bin/dagu history config-backup --last 7d`. Archives are stored at
`/mnt/sda1/backups/pi-config/pi-config-*.tar.gz`. Archives created after this integration include
a SQLite `.backup` snapshot of `jobs.sqlite3`, verify it with `PRAGMA integrity_check` and a size
check, and include the runtime configuration. Older archives do not contain this snapshot. The job
never archives a live jobs database or its WAL. These archives contain delivery secrets; keep them
private.

To restore the request history, choose the desired archive and verify it contains the jobs snapshot
before stopping the service. Only archives created after this backup integration contain it. Run as
`neerajsamtani` (UID 1000, the container's file owner):

```sh
set -euo pipefail
umask 077
cd ~/rss-to-kindle
test "$(id -u)" = 1000
ARCHIVE=$(ls -1t /mnt/sda1/backups/pi-config/pi-config-*.tar.gz | sed -n '1p')
test -n "$ARCHIVE"
test -f "$ARCHIVE"
tar -tzf "$ARCHIVE" | grep -Fx 'mnt/sda1/backups/.staging/sqlite/jobs.sqlite3.snapshot'
STAMP=$(date +%Y%m%d-%H%M%S)
DATA="$HOME/rss-to-kindle/data"
SNAP="$DATA/.jobs.sqlite3.snapshot-$STAMP"
RESTORE="$DATA/.jobs.sqlite3.restore-$STAMP"
SAVED="$DATA/pre-restore-$STAMP"
test ! -e "$SNAP"
test ! -e "$RESTORE"
test ! -e "$SAVED"

tar -xOf "$ARCHIVE" mnt/sda1/backups/.staging/sqlite/jobs.sqlite3.snapshot > "$SNAP"
test -s "$SNAP"
test "$(stat -c '%s' "$SNAP")" -ge 512
test "$(sqlite3 "$SNAP" 'PRAGMA integrity_check;')" = ok
sqlite3 "$SNAP" ".backup '$RESTORE'"
test "$(sqlite3 "$RESTORE" 'PRAGMA integrity_check;')" = ok
chmod 600 "$SNAP" "$RESTORE"
echo "Snapshot and restore copy passed integrity checks."

QUEUED=$(sqlite3 "$SNAP" "SELECT COUNT(*) FROM jobs WHERE status = 'queued';")
if [ "$QUEUED" -gt 0 ]; then
  echo "The snapshot has queued jobs; they will auto-process after startup. Review first:"
  sqlite3 -header -column "$SNAP" \
    "SELECT id, status, title FROM jobs WHERE status = 'queued' ORDER BY created_at;"
  read -r -p 'Type RESUME only after checking for duplicate delivery: ' CONFIRM
  [ "$CONFIRM" = RESUME ]
fi

wait_for_health() {
  for attempt in {1..18}; do
    if curl -fsS --max-time 5 -o /dev/null http://127.0.0.1:18788/api/health 2>/dev/null; then
      curl -fsS http://127.0.0.1:18788/api/health
      return 0
    fi
    if [ "$attempt" -lt 18 ]; then sleep 5; fi
  done
  return 1
}

docker compose stop --timeout 240 web
mkdir -m 700 "$SAVED"
for suffix in "" -wal -shm; do
  file="$DATA/jobs.sqlite3$suffix"
  if [ -e "$file" ]; then mv -- "$file" "$SAVED/"; fi
done
mv "$RESTORE" "$DATA/jobs.sqlite3"
chmod 600 "$DATA/jobs.sqlite3"
if docker compose up -d web && wait_for_health; then
  echo "Restore is healthy; old database files are preserved in $SAVED."
else
  echo "Restored database failed health; restoring the saved database files." >&2
  docker compose stop --timeout 240 web
  for suffix in "" -wal -shm; do
    current="$DATA/jobs.sqlite3$suffix"
    if [ -e "$current" ]; then mv -- "$current" "$DATA/jobs.sqlite3.failed-$STAMP$suffix"; fi
    saved="$SAVED/jobs.sqlite3$suffix"
    if [ -e "$saved" ]; then mv -- "$saved" "$DATA/"; fi
  done
  docker compose up -d web
  wait_for_health
  echo "Previous database restored and healthy; investigate before retrying the restore."
  exit 1
fi
```

Keep `pre-restore-$STAMP` until the restored request history is verified, then remove it and the
temporary `SNAP` file. For a full Pi rebuild, clone application source from Git, restore
`.env.runtime` from the archive with mode `600`, and let bootstrap build and pin the checked-out
commit. Do not reuse the archived `.env` pin unless you have also restored that exact commit and
image.

## iPhone Share Sheet shortcut

Create a Shortcut that accepts **URLs** from the Share Sheet, then add these actions in order:

1. **URL Encode** the Shortcut Input.
2. Build `https://raspberrypi.taild579e4.ts.net/rss-to-kindle/?url=` followed by the encoded value.
3. **Open URLs** with that address.

Enable the shortcut in the Share Sheet. Sharing an article opens the app with its URL filled in;
the page does not submit it automatically. Review the article and tap the send button yourself.
The phone must be connected to Tailscale.

## Refreshing Substack cookies

On the Mac, while logged into the relevant publication in Chrome, import the custom-domain cookie
for a current article URL:

```sh
uv run rss-to-kindle substack-login --from-browser chrome --url 'https://publication.substack.com/p/article'
```

This updates the Mac's ignored `.env`. Transfer only that file to the Pi runtime path, then
recreate the container so it reads the refreshed cookie. Do not print or copy the cookie into
logs. The app drains its active job before the container stops; queued jobs stay in SQLite:

```sh
chmod 600 .env
rsync -av .env neerajsamtani@raspberrypi:~/rss-to-kindle/.env.runtime
ssh neerajsamtani@raspberrypi 'chmod 600 "$HOME/rss-to-kindle/.env.runtime" && cd "$HOME/rss-to-kindle" && docker compose up -d --force-recreate web'
```

Verify `/api/health` after the restart. The refreshed file must remain mode `600`.
