# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python CLI tool that monitors Substack RSS feeds, fetches full paywalled article content using session cookies, and emails them to a Kindle device as EPUB attachments with embedded images and generated covers.

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
uv run rss-to-kindle login --from-browser chrome
uv run rss-to-kindle list               # Show configured feed URLs
uv run rss-to-kindle add <url>          # Add a feed URL to .env
uv run rss-to-kindle remove <url>       # Remove a feed URL from .env
uv run rss-to-kindle history            # Show recently sent articles (--limit N)
uv run rss-to-kindle preview <url>      # Generate EPUB locally without sending (-o path)
```

### Testing

No test framework is currently configured. Add pytest to pyproject.toml when needed:

```bash
# Add pytest to dependencies, then:
uv sync

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_feed.py -v

# Run a single test
uv run pytest tests/test_feed.py::test_fetch_feed -v
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

## Architecture

The pipeline flows linearly: **feed.py → fetcher.py → extractor.py → kindle.py**, orchestrated by **cli.py**.

- `config.py` loads `.env` via python-dotenv and validates required fields
- `feed.py` parses RSS feeds with feedparser, returns `FeedArticle` dataclasses
- `fetcher.py` fetches full article HTML via httpx using the Substack `substack.sid` session cookie
- `extractor.py` extracts clean article content and downloads images (see below)
- `kindle.py` builds an EPUB with cover image and sends as email attachment via SMTP
- `state.py` tracks sent article URLs in `~/.rss-to-kindle/sent.json` to prevent duplicates
- `cli.py` wires everything together with click; `_process_feeds()` is the main loop body

### Image processing pipeline (extractor.py)

Substack wraps images in deeply nested markup (`div.captioned-image-container > figure > a > div > picture > img`) that readability-lxml strips during content extraction. To work around this:

1. `_simplify_images()` runs **before** readability — it finds `captioned-image-container` divs, extracts the real S3 URL from each `<img>`'s `data-attrs` JSON, and replaces the entire container with a simple `<figure><img><figcaption></figure>`.
2. Readability then preserves these simplified elements during content extraction.
3. `_download_images()` runs **after** readability — it downloads each image, generates an md5-based filename, and rewrites `src` to local EPUB paths.

## Configuration

All config is via environment variables (`.env` file). See `.env.example` for the template. `SUBSTACK_FEEDS` is comma-separated for multiple feeds.

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
