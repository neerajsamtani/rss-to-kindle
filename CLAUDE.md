# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python CLI tool that monitors Substack RSS feeds, fetches full paywalled article content using session cookies, and emails them to a Kindle device as EPUB attachments with embedded images and generated covers.

## Commands

```bash
# Install dependencies
uv sync

# Run the CLI
uv run rss-to-kindle fetch          # One-shot: check feeds, fetch new articles, send to Kindle
uv run rss-to-kindle fetch --latest # Only fetch the latest article from each feed
uv run rss-to-kindle poll           # Continuous: run fetch every POLL_INTERVAL_MINUTES
uv run rss-to-kindle login --from-browser chrome

# Install in editable mode after pyproject.toml changes
uv pip install -e .                 # Install in editable mode after changing pyproject.toml
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
- Line length: 100 characters
- Target: Python 3.12
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

### Python Version & Types
- Python 3.12+ required
- Use type hints on all function signatures: `def func() -> str:`
- Use `list[str]` instead of `List[str]` (PEP 585 style)
- Use `dict[str, int]` instead of `Dict[str, int]`
- Use dataclasses for data containers with `@dataclass`

### Naming Conventions
- **Functions/variables**: `snake_case` (e.g., `fetch_article_html`)
- **Classes**: `PascalCase` (e.g., `FeedArticle`)
- **Constants**: `UPPER_CASE` (e.g., `STATE_FILE`)
- **Private functions**: `_leading_underscore` (e.g., `_simplify_images`)
- **Modules**: `snake_case` without underscores preferred (e.g., `feed.py`)

### Imports
Group and order imports:
1. Standard library (e.g., `json`, `os`, `datetime`)
2. Third-party packages (e.g., `click`, `httpx`, `feedparser`)
3. Local modules (e.g., `from .extractor import Article`)

Use absolute imports for local modules: `from .module import Thing`

### Error Handling
- For CLI errors, use `click.echo(f"Error: {e}", err=True)` and `continue`
- For fatal errors, use `raise click.ClickException("message")`
- For library code, raise appropriate exceptions (e.g., `ValueError`, `RuntimeError`)
- Catch exceptions at loop boundaries to prevent one bad item from failing the batch

### Comments & Documentation
- Docstrings for all public functions using `"""triple quotes"""`
- Comments explain *why*, not *what* — keep them minimal
- Add comments for complex workarounds (see extractor.py for examples)

### Dependencies

Key libraries in use:
- `click` — CLI framework
- `httpx` — HTTP client
- `feedparser` — RSS parsing
- `readability-lxml` — HTML content extraction
- `ebooklib` — EPUB generation
- `lxml` — HTML parsing
- `Pillow` — Image processing
- `browser-cookie3` — Browser cookie extraction

## Development Philosophy

**"Every line of code is a liability"**

- **Prefer editing over adding**: Modify existing functions to be more extensible rather than creating new ones
- **Minimize code**: Write only what's necessary to accomplish the task - no more, no less
- **Prioritize readability**: Code should be easily understood by developers jumping into the codebase
- **Prudent comments**: Only add comments for things that are not obvious when reading the code, or when summarizing large chunks of code when a comment would greatly improve readability. Comments should generally talk about _why_ the code is doing what it's doing and not just _what_ the code is doing
- **Favor maintainability**: Simple, clear solutions over clever ones
- **Keep it accessible**: New developers should be able to quickly understand and make changes
