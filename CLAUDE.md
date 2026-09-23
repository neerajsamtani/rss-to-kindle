# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python CLI tool that monitors RSS feeds, fetches full article content, and emails them to a Kindle device as EPUB attachments with embedded images and generated covers. It also has a private web form for sending a single URL. Supports any RSS feed; Substack-specific features (session cookies for paywalled content, HTML workarounds) activate automatically when the feed's generator is Substack.

## Commands

```bash
# Install dependencies
uv sync

# Run the CLI
uv run rss-to-kindle fetch              # One-shot: check feeds, fetch new articles, send to Kindle
uv run rss-to-kindle fetch --latest     # Only fetch the latest article from each feed
uv run rss-to-kindle fetch --days 7     # Only process articles from the last 7 days (default: 3)
uv run rss-to-kindle poll               # Continuous: run fetch every POLL_INTERVAL_MINUTES
uv run rss-to-kindle init                # Interactive first-run setup (Kindle email + optional Substack login)
uv run rss-to-kindle substack-login --from-browser chrome
uv run rss-to-kindle substack-login --from-browser chrome --print  # also print values for GitHub secrets
uv run rss-to-kindle list               # Show configured feed URLs
uv run rss-to-kindle add <url>          # Add a feed URL to .env
uv run rss-to-kindle remove <url>       # Remove a feed URL from .env
uv run rss-to-kindle history            # Show recently sent articles (--limit N)
uv run rss-to-kindle preview <url>      # Generate EPUB locally without sending (-o path)
```

For a local web-service command and authenticated health/API probes, see the **One-off web
service** section in [README.md](README.md). Keep local Gunicorn bound to loopback; the UI trusts
`Tailscale-User-Login` only when a trusted proxy supplies it.

### Testing

Regression tests use Python's standard-library unittest runner:

```bash
# Run all tests
uv run python -m unittest discover -s tests -v
```

### Linting & Formatting

Ruff is configured in pyproject.toml with:
- Line length: 100 characters (E501 ignored — the formatter handles wrapping)
- Target: Python 3.12
- Double quotes enforced
- Rules: E, F, I (imports), W, UP (pyupgrade), B (bugbear)

```bash
# Check for linting issues
uv run ruff check .

# Fix auto-fixable issues
uv run ruff check . --fix

# Format all code
uv run ruff format .

# Check formatting without applying
uv run ruff format . --check
```

### Pre-push Hooks

pre-commit runs ruff on every push to block issues before they reach CI:

```bash
# Install hooks (run once after cloning)
uv run pre-commit install --hook-type pre-push

# Run hooks manually on all files
uv run pre-commit run --all-files

# Skip hooks temporarily (not recommended)
git push --no-verify

# Update hook versions
uv run pre-commit autoupdate
```

## CI/CD

- **Lint** (`.github/workflows/lint.yml`): Runs `ruff check` and `ruff format --check` on PRs and pushes to main.
- **Fetch** (`.github/workflows/fetch.yml`): Scheduled every 6 hours (+ manual dispatch) to run `fetch` and send new articles. Uses `actions/cache` to persist `~/.rss-to-kindle/sent.json` between runs. All config is via repository secrets.

## Raspberry Pi deployment

See [deploy/pi/README.md](deploy/pi/README.md) for Pi installation, Dagu deployment, host
integration, health checks, backups, and recovery. Deployment uses the tracked public Git clone
and builds an ARM64 image on the Pi; `.env.runtime` and the ext4-backed `data/` directory are
untracked. Do not rsync tracked application code or run `git clean` on the Pi. The stable poller
is installed outside the checkout so a failed candidate can restore the prior image and Compose
file before boot reconciliation starts the stack.

The Pi packaging and controller checks run with the regular suite:

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check .
for file in scripts/*.sh; do bash -n "$file"; done
```

## Architecture

The pipeline flows linearly: **feed.py → fetcher.py → extractor.py → kindle.py**, orchestrated by **cli.py**.

- `config.py` loads `.env` via python-dotenv and validates required fields
- `feed.py` parses RSS feeds with feedparser, returns `FeedArticle` dataclasses
- `fetcher.py` fetches full article HTML via httpx (attaches Substack session cookies only for Substack feeds)
- `extractor.py` extracts clean article content and downloads images (see below)
- `kindle.py` builds an EPUB with cover image and sends as email attachment via SMTP
- `state.py` tracks sent article URLs in `~/.rss-to-kindle/sent.json` to prevent duplicates
- `cli.py` wires everything together with click; `_process_feeds()` is the main loop body

### One-off web service

The web form uses the same fetch, extraction, and delivery code as `preview` and the RSS path.
Change these modules for the corresponding behavior:

- `fetcher.py`: safe public HTTP requests, redirects, and scoped Substack cookies.
- `extractor.py`: article selection, cleanup, and embedded images.
- `pipeline.py`: one-URL orchestration, progress stages, and safe delivery errors.
- `kindle.py`: EPUB generation and SMTP delivery.
- `job_queue.py`: persistent SQLite history, one serial worker, and shutdown draining.
- `web.py`: Flask routes, owner allowlist, same-origin POST checks, and status API.
- `web_templates/`, `web_static/app.js`, and `web_static/style.css`: rendered pages and browser UI.
- `config.py`: shared delivery and cookie settings; one-off web actions do not require `FEEDS`.

The owner allowlist is `WEB_OWNER_LOGIN` and must match the trusted `Tailscale-User-Login`
header. The app must stay behind a private proxy; do not trust that header from a public client.
`GET /api/health` returns generic database and worker health without secrets or article data.
`GET /api/jobs` lists recent requests,
`GET /api/jobs/<id>` returns one status record, and `GET /jobs/<id>` renders its detail page.
Keep one Gunicorn worker because the SQLite queue has one in-process worker thread.

### Image processing pipeline (extractor.py)

Substack wraps images in deeply nested markup (`div.captioned-image-container > figure > a > div > picture > img`) that readability-lxml strips during content extraction. To work around this:

1. `_simplify_images()` runs **before** readability — it finds `captioned-image-container` divs, extracts the real S3 URL from each `<img>`'s `data-attrs` JSON, and replaces the entire container with a simple `<figure><img><figcaption></figure>`.
2. Readability then preserves these simplified elements during content extraction.
3. `_download_images()` runs **after** readability — it downloads each image, generates an md5-based filename, and rewrites `src` to local EPUB paths.

## Configuration

All config is via environment variables (`.env` file). See `.env.example` for the template. `FEEDS` is comma-separated for multiple feeds. `SUBSTACK_SESSION_COOKIE` and `SUBSTACK_CONNECT_COOKIES` are only needed for paid Substack content — Substack uses `substack.sid` on `.substack.com` and per-domain `connect.sid` cookies on custom newsletter domains. Both cookie env vars store JSON with `value` and `expires` fields (e.g. `SUBSTACK_SESSION_COOKIE={"value": "s%3A...", "expires": 1234567890}`, `SUBSTACK_CONNECT_COOKIES={"newsletter.example.com": {"value": "s%3A...", "expires": 1234567890}}`). The CLI warns when cookies are within 14 days of expiring, and emails the sender address (at most once per 24h) when they are expiring or expired.

Refresh cookies from a logged-in browser, including the per-domain cookie for a custom publication,
with `uv run rss-to-kindle substack-login --from-browser chrome --url '<article-url>'`. Use
`uv run rss-to-kindle preview '<article-url>'` to inspect extraction and build an EPUB without
sending it. Treat imported cookie values as secrets; do not print them into logs or chat.

## Code Style Guidelines

- Python 3.12+ required; use PEP 585 type hints (`list[str]`, not `List[str]`)
- Use type hints on all function signatures
- Use dataclasses for data containers
- Ruff enforces formatting, import ordering, and lint rules — see `pyproject.toml` for config

### Error Handling
- For CLI errors, use `click.echo(f"Error: {e}", err=True)` and `continue`
- For fatal errors, use `raise click.ClickException("message")`
- For library code, raise appropriate exceptions (e.g., `ValueError`, `RuntimeError`)
- Catch exceptions at loop boundaries to prevent one bad item from failing the batch

## Development Philosophy

**"Every line of code is a liability"**

- **Prefer editing over adding**: Modify existing functions to be more extensible rather than creating new ones
- **Minimize code**: Write only what's necessary to accomplish the task - no more, no less
- **Prioritize readability**: Code should be easily understood by developers jumping into the codebase
- **Prudent comments**: Only add comments for things that are not obvious when reading the code, or when summarizing large chunks of code when a comment would greatly improve readability. Comments should generally talk about _why_ the code is doing what it's doing and not just _what_ the code is doing
- **Favor maintainability**: Simple, clear solutions over clever ones
- **Keep it accessible**: New developers should be able to quickly understand and make changes
- **Minimize Substack-specific code**: Keep Substack-specific logic (session cookies, `captioned-image-container` workarounds) isolated to where it's strictly needed. Generic operations like downloading public images or parsing standard meta tags should not depend on Substack auth. This makes it easier to extend the tool to other RSS feeds.
