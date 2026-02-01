# rss-to-kindle

Monitors RSS feeds, fetches full article content, and emails them to a Kindle device as EPUB attachments with embedded images and generated covers. Supports any RSS feed; Substack-specific features (session cookies for paywalled content) activate automatically.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/neerajsamtani/rss-to-kindle.git
cd rss-to-kindle
uv sync
```

## Setup

### 1. Configure environment

```sh
cp .env.example .env
```

Edit `.env` with your values:

| Variable | Description |
|---|---|
| `FEEDS` | Comma-separated RSS feed URLs |
| `SUBSTACK_SESSION_COOKIE` | JSON with Substack `substack.sid` cookie value and expiry (e.g. `{"value": "s%3A...", "expires": 1234567890}`) — only needed for paid content (see step 2) |
| `SUBSTACK_CONNECT_COOKIES` | JSON dict of per-domain `connect.sid` cookies with expiry (e.g. `{"newsletter.example.com": {"value": "s%3A...", "expires": 1234567890}}`) — only needed for paid content on custom domains |
| `KINDLE_EMAIL` | Your Kindle's email address (e.g. `name@kindle.com`) |
| `SENDER_EMAIL` | Email address to send from |
| `SENDER_PASSWORD` | App password for the sender email |
| `SMTP_HOST` | SMTP server (default: `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (default: `587`) |
| `POLL_INTERVAL_MINUTES` | Minutes between poll cycles (default: `30`) |

### 2. Import your Substack session cookies (optional — only for paid content)

Log into [substack.com](https://substack.com) in your browser, then run:

```sh
uv run rss-to-kindle substack-login --from-browser chrome
```

Supported browsers: `chrome`, `firefox`, `opera`, `edge`, `chromium`.

This saves both `substack.sid` and `connect.sid` cookies (with expiry dates) to your `.env` file automatically. You'll be warned when cookies are about to expire or have already expired.

### 3. Approve the sender email on Amazon

Add your `SENDER_EMAIL` address to your [Amazon Approved Personal Document E-mail List](https://www.amazon.com/hz/mycd/myx#/home/settings/payment) so Kindle accepts the attachments.

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
```

Already-sent articles are tracked in `~/.rss-to-kindle/sent.json` to avoid duplicates.
