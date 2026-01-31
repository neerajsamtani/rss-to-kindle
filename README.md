# rss-to-kindle

Monitors Substack RSS feeds, fetches full paywalled article content using session cookies, and emails them to a Kindle device as EPUB attachments with embedded images and generated covers.

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
| `SUBSTACK_FEEDS` | Comma-separated Substack RSS feed URLs |
| `SUBSTACK_SESSION_COOKIE` | Your Substack session cookie (see step 2) |
| `KINDLE_EMAIL` | Your Kindle's email address (e.g. `name@kindle.com`) |
| `SENDER_EMAIL` | Email address to send from |
| `SENDER_PASSWORD` | App password for the sender email |
| `SMTP_HOST` | SMTP server (default: `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (default: `587`) |
| `POLL_INTERVAL_MINUTES` | Minutes between poll cycles (default: `30`) |

### 2. Import your Substack session cookie

Log into [substack.com](https://substack.com) in your browser, then run:

```sh
uv run rss-to-kindle login --from-browser chrome
```

Supported browsers: `chrome`, `firefox`, `opera`, `edge`, `chromium`.

This saves the cookie to your `.env` file automatically.

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
