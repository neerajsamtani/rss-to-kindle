# rss-to-kindle

Monitors RSS feeds, fetches full article content, and emails them to a Kindle device as EPUB attachments. Also sends you paywalled Substack articles if you're subscribed to them.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/neerajsamtani/rss-to-kindle.git
cd rss-to-kindle
uv sync
```

## Setup

### 1. Configure sender email credentials

```sh
cp .env.example .env
```

Edit `.env` and fill in the SMTP / sender fields:

| Variable | Description |
|---|---|
| `SENDER_EMAIL` | Email address to send from (e.g. your Gmail address) |
| `SENDER_PASSWORD` | An [app password](https://myaccount.google.com/apppasswords) for that email |
| `SMTP_HOST` | SMTP server (default: `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (default: `587`) |

### 2. Run the interactive setup

```sh
uv run rss-to-kindle init
```

This walks you through:

1. Finding and saving your Kindle email address, provided by Amazon
2. Approving the sender email on [Amazon's Approved Personal Document E-mail List](https://www.amazon.com/hz/mycd/digital-console/contentlist/pdocs/dateDsc)
3. Adding RSS feed URLs
4. Optionally importing Substack session cookies from your browser (only needed for paid Substack content)

All values are saved to `.env` automatically.

### Manual alternative

If you prefer to skip `init`, you can edit `.env` directly. See [`.env.example`](.env.example) for all available variables. For Substack paid content, import cookies with:

```sh
uv run rss-to-kindle substack-login --from-browser chrome
```

Supported browsers: `chrome`, `firefox`, `opera`, `edge`, `chromium`.

## Usage

```sh
# Fetch new articles and send to Kindle
uv run rss-to-kindle fetch

# Only send the latest article from each feed
uv run rss-to-kindle fetch --latest

# Only process articles from the last 7 days (default: 3)
uv run rss-to-kindle fetch --days 7

# Continuously poll for new articles
uv run rss-to-kindle poll

# Learn more
uv run rss-to-kindle --help
```

Already-sent articles are tracked in `~/.rss-to-kindle/sent.json` to avoid duplicates.

## One-off web service

The private web form queues a single URL and keeps its status in SQLite. It needs
`KINDLE_EMAIL`, `SENDER_EMAIL`, and `SENDER_PASSWORD` in `.env`, but does not require `FEEDS`.
For local API debugging, set the owner login and use one Gunicorn worker:

```sh
WEB_OWNER_LOGIN=you@example.com WEB_BASE_PATH="" \
  WEB_DB_PATH=/tmp/rss-to-kindle-jobs.sqlite3 \
  uv run gunicorn --workers 1 --bind 127.0.0.1:8000 'rss_to_kindle.web:create_app()'

# Health is internal and does not include credentials or article data
curl -fsS http://127.0.0.1:8000/api/health

# Owner-protected API requests need the trusted identity header
curl -fsS -H 'Tailscale-User-Login: you@example.com' \
  http://127.0.0.1:8000/api/jobs
```

The browser UI requires a trusted proxy to set `Tailscale-User-Login`; deployment uses
Tailscale Serve. Keep Gunicorn bound to loopback and do not expose a client-controlled identity
header. See [the Pi guide](deploy/pi/README.md) for deployment and recovery.

To inspect extraction without sending email, run `uv run rss-to-kindle preview <url>`. Refresh
Substack cookies from a logged-in browser with
`uv run rss-to-kindle substack-login --from-browser chrome --url '<article-url>'`.

## Automated fetching with GitHub Actions

The included workflow (`.github/workflows/fetch.yml`) runs `fetch` every 6 hours and caches `sent.json` between runs so articles are never sent twice. To enable it:

1. Fork this repo (or push the code to your own GitHub repository).
2. If you forked, go to the **Actions** tab and enable workflows (GitHub disables them by default on forks).
3. Add the following secrets under **Settings → Secrets and variables → Actions**:

| Secret | Description |
|---|---|
| `SENDER_EMAIL` | Same as your `.env` value |
| `SENDER_PASSWORD` | Same as your `.env` value |
| `SMTP_HOST` | SMTP server (e.g. `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (e.g. `587`) |
| `KINDLE_EMAIL` | Your Kindle email address |
| `FEEDS` | Comma-separated RSS feed URLs |
| `SUBSTACK_SESSION_COOKIE` | *(optional)* JSON cookie value from `.env` |
| `SUBSTACK_CONNECT_COOKIES` | *(optional)* JSON cookie value from `.env` |

4. The workflow runs automatically on schedule. You can also trigger it manually from the **Actions** tab via "Run workflow".

## Raspberry Pi web app

The Raspberry Pi deployment provides a private Tailscale web form for queueing an article URL
and sending it to Kindle. Setup, automatic deployment, backups, Substack cookie refresh, and the
iPhone Share Sheet shortcut are documented in [deploy/pi/README.md](deploy/pi/README.md).
