# rss-to-kindle

Monitors RSS feeds, fetches article content, and emails it to a Kindle device as EPUB
attachments. Substack session cookies can be used for paid publications you can access.

## Install and configure

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/neerajsamtani/rss-to-kindle.git
cd rss-to-kindle
uv sync
cp .env.example .env
```

Edit `.env` and provide `SENDER_EMAIL` and `SENDER_PASSWORD` before running setup. For Gmail,
use an [app password](https://myaccount.google.com/apppasswords). `SMTP_HOST` and `SMTP_PORT`
default to Gmail's SMTP server and port; change them for another provider.

```sh
uv run rss-to-kindle init
```

`init` guides you through saving your Kindle address, approving your sender address with Amazon,
adding feeds, and optionally importing Substack cookies. You can edit `.env` directly; see
[.env.example](.env.example) for the available settings. The `.env` file is for the CLI. Docker
Compose uses a separate
`.env.runtime` file; see the [Docker deployment guide](deploy/README.md).

To import Substack cookies later, run `uv run rss-to-kindle substack-login --from-browser chrome`.
Supported browsers include Chrome, Firefox, Opera, Edge, and Chromium.

## Use

```sh
uv run rss-to-kindle fetch              # Fetch and send new articles
uv run rss-to-kindle fetch --latest     # Only the latest article from each feed
uv run rss-to-kindle fetch --days 7     # Articles from the last seven days
uv run rss-to-kindle poll               # Repeat fetches at the configured interval
uv run rss-to-kindle --help
```

Already sent article URLs are recorded in `~/.rss-to-kindle/sent.json` to avoid sending them
again. `uv run rss-to-kindle preview <url>` builds an EPUB locally without sending it.

## GitHub Actions

The included workflow runs `fetch` every six hours and can also be started manually from the
Actions tab. Enable Actions after forking, then add these repository secrets under **Settings →
Secrets and variables → Actions**:

| Secret | Description |
|---|---|
| `SENDER_EMAIL`, `SENDER_PASSWORD` | Sender credentials |
| `SMTP_HOST`, `SMTP_PORT` | SMTP server and port |
| `KINDLE_EMAIL` | Kindle delivery address |
| `FEEDS` | Comma-separated RSS feed URLs |
| `SUBSTACK_SESSION_COOKIE`, `SUBSTACK_CONNECT_COOKIES` | Optional paid Substack cookies |

## Private one-off web service

The web form queues one URL at a time and records request status in SQLite. It needs
`KINDLE_EMAIL`, `SENDER_EMAIL`, and `SENDER_PASSWORD`, but does not require `FEEDS`. For local
development, bind Gunicorn to loopback and configure the owner login:

```sh
WEB_OWNER_LOGIN=you@example.com WEB_BASE_PATH="" \
  WEB_DB_PATH=/tmp/rss-to-kindle-jobs.sqlite3 \
  uv run gunicorn --workers 1 --bind 127.0.0.1:8000 'rss_to_kindle.web:create_app()'

# Health is generic and does not include credentials or article data
curl -fsS http://127.0.0.1:8000/api/health

# Job APIs require the identity header supplied by the trusted proxy
curl -fsS -H 'Tailscale-User-Login: you@example.com' \
  http://127.0.0.1:8000/api/jobs
```

The app trusts `Tailscale-User-Login` only when a trusted private proxy supplies it. Keep the
service behind that proxy; the header alone is not authentication. See the
[Docker deployment guide](deploy/README.md) for a generic deployment outline.
