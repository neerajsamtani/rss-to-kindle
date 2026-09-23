# Raspberry Pi deployment

The Pi runs one ARM64 Python 3.12 container. Gunicorn has one synchronous web worker; its SQLite
job worker runs in that process. SQLite lives at `~/rss-to-kindle/data/jobs.sqlite3` on the Pi's
ext4 root filesystem. The web container binds only to `127.0.0.1:18788`; Tailscale Serve owns the
`/rss-to-kindle/` path on HTTPS 443. The owner allowlist is the Pi's Tailscale identity,
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
rsync -av --no-perms --chmod=F600 .env neerajsamtani@raspberrypi:~/rss-to-kindle/.env.runtime
```

On the Pi, verify the secret file and data filesystem, then build and start the initial tested
image. `FEEDS` is optional; `SENDER_EMAIL`, `SENDER_PASSWORD`, and `KINDLE_EMAIL` are required.

```sh
cd ~/rss-to-kindle
chmod 600 .env.runtime
sudo -n true
./scripts/bootstrap-pi-stack.sh
```

Bootstrap refuses a non-ext4 database volume or a mismatched existing image pin. It builds with
the Docker test gate enabled, waits for `/api/health`, and installs the stable deployment
controller under `~/.local/bin`, outside the Git checkout.

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

The health endpoint returns only status, database, and worker state. Tailscale must be connected
on the client device before opening
`https://raspberrypi.taild579e4.ts.net/rss-to-kindle/`.

## Ongoing deployment and recovery

Dagu checks public `origin/main` every five minutes. It compares the fetched SHA with the last
healthy image, refuses a dirty tracked checkout, and builds the ARM image with tests before
stopping the running app. The Compose `.env` contains only the last successful `IMAGE_TAG`; the
candidate tag is passed only to its own Compose operation. A health failure restores both the
previous image pin and saved Compose file immediately. A failed SHA is held until main advances
or an operator clears the marker after inspecting the cause:

```sh
rm ~/.config/dagu/.rss-to-kindle-deploy-failed-sha
```

Never run `git clean` or discard untracked files on the Pi. For a manual deployment, inspect
`docker compose logs --tail 100 web`, the stable controller's `/tmp/rss-to-kindle-deploy.log`,
and the failed SHA marker before clearing it. Boot reconciliation automatically recovers any
transaction interrupted by power loss before starting Compose.

The nightly `config-backup` job now snapshots `data/jobs.sqlite3` through SQLite's online backup
API, verifies integrity and a minimum size, then archives the snapshot with the runtime config.
It does not copy the live database/WAL files. Do not move SQLite data to `/mnt/sda1` (exFAT); keep
the live database on ext4.

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
logs:

```sh
rsync -av --no-perms --chmod=F600 .env neerajsamtani@raspberrypi:~/rss-to-kindle/.env.runtime
ssh neerajsamtani@raspberrypi 'cd ~/rss-to-kindle && chmod 600 .env.runtime && docker compose up -d --force-recreate web'
```

Compose honors the 240-second stop grace period while the app stops claiming jobs and drains its
current send. Queued jobs remain in the ext4-backed SQLite database.
