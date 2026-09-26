# Docker deployment

This guide runs the one-off web service with Docker Compose. It assumes Docker Engine and the
Compose plugin are installed on a Linux host. The example keeps the service on loopback so a
trusted private proxy, such as Tailscale Serve, can provide the identity header.

## Configure and start

Create the runtime environment file. It contains delivery credentials and the identity allowed
to use the web form; keep it private and out of version control.

```sh
cp .env.example .env.runtime
chmod 600 .env.runtime
```

Edit `.env.runtime`: set the SMTP and Kindle settings, then set `WEB_OWNER_LOGIN` to the exact
login the trusted proxy sends in `Tailscale-User-Login`. Set `WEB_BASE_PATH` to the path where
the proxy will publish the app (the default is `/rss-to-kindle`). `FEEDS` is not needed for the
web service. Cookie settings are optional and only needed for paid Substack content.

The service runs as UID and GID 1000 and writes its SQLite database under `./data`. Prepare the
directory for that account, then build and start:

```sh
mkdir -p data
chmod 700 data
sudo chown 1000:1000 data
docker compose build
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:18788/api/health
```

To run the project tests during the image build, use
`docker compose build --build-arg RUN_TESTS=1`.

The Compose file uses raw env-file parsing to preserve cookie values literally; use Docker
Compose 2.30.0 or later.

Compose publishes the container on `127.0.0.1:18788`. For example, with Tailscale Serve and the
default base path:

```sh
tailscale serve --bg --https=443 --set-path=/rss-to-kindle/ http://127.0.0.1:18788
```

For another base path, set the same `WEB_BASE_PATH` in `.env.runtime` and `--set-path` in the
Serve command. Do not make the port public or accept a client-provided
`Tailscale-User-Login` header. The app's owner check depends on that header coming from the
trusted proxy; it does not provide standalone login or make a public proxy safe.

The container uses one Gunicorn worker for its in-process serial queue, and has a read-only root
filesystem with dropped capabilities. Compose includes a health check and a shutdown window for
draining active work.

## Operate and update

```sh
docker compose logs -f web
docker compose restart web
docker compose down
```

After pulling a new version of the application, rebuild and recreate the service:

```sh
docker compose build --pull
docker compose up -d
```

The default image tag is `local`; set `IMAGE_TAG` in the shell or a Compose `.env` file if you
need a different local tag. Compose's `.env` is used for interpolation such as image tags. It is
distinct from `.env.runtime`, which supplies application settings and secrets to the container.

## Back up and restore

Stop the service for a simple consistent backup, then copy the whole `data/` directory to
protected storage. If using a live backup, use SQLite's online backup mechanism or filesystem
snapshots that preserve SQLite consistency; copying only the database file while it is active
can produce an unusable backup. Restore the complete directory with ownership set to UID/GID
1000. Stop the service before restoring, then review queued and recent requests before restarting
to avoid sending an article again. Keep backup copies protected because job history may contain
submitted URLs.

## Refresh Substack cookies and share from iPhone

Refresh cookies on a desktop where you are signed into the publication, using the CLI's
`substack-login` command. Copy the updated cookie values into `.env.runtime` securely. After
changing cookie values, recreate the service so Docker loads the updated runtime file. Do not
paste cookie values into logs or chat:

```sh
docker compose up -d --force-recreate web
```

An iPhone Shortcut can take the shared URL from the Share Sheet, URL Encode it, then open
`https://<your-tailnet-host>/rss-to-kindle/?url=<encoded-url>`. The app pre-fills the article
URL; review it and tap **Send to Kindle** on the page. Connect the iPhone to Tailscale first.
Replace the path if you configured a different `WEB_BASE_PATH`.
