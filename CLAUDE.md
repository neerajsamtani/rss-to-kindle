# CLAUDE.md

Developer notes for the rss-to-kindle repository.

## Setup and commands

Requires Python 3.12+. Install dependencies with `uv sync`.

```sh
uv run rss-to-kindle fetch
uv run rss-to-kindle fetch --latest
uv run rss-to-kindle fetch --days 7
uv run rss-to-kindle poll
uv run rss-to-kindle init
uv run rss-to-kindle list
uv run rss-to-kindle add <url>
uv run rss-to-kindle remove <url>
uv run rss-to-kindle history
uv run rss-to-kindle preview <url>
```

For a local web service, bind Gunicorn to `127.0.0.1`, use one worker, and configure
`WEB_OWNER_LOGIN`. The web app trusts `Tailscale-User-Login` only when it comes from a trusted
private proxy. Docker deployment instructions are in [deploy/README.md](deploy/README.md).

## Tests and lint

The unittest suite covers configuration, extraction and pipeline behavior, and the web service:

```sh
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format . --check
```

Install the pre-push hooks once with `uv run pre-commit install --hook-type pre-push`.

CI runs Ruff checks and the unittest suite on pushes to `main` and pull requests. The scheduled
fetch workflow runs every six hours and can be started manually.

## Architecture

The CLI coordinates the feed and article pipeline: `feed.py` parses RSS entries, `fetcher.py`
downloads article pages, `extractor.py` cleans content and images, and `kindle.py` builds and
sends EPUBs. `pipeline.py` coordinates one-off URL delivery. `state.py` tracks sent RSS article
URLs in `~/.rss-to-kindle/sent.json`.

The web service shares article fetching, extraction, EPUB generation, and SMTP delivery with the
CLI. `web.py` serves the form and APIs; `job_queue.py` stores requests in SQLite and runs one
serial worker. Run one Gunicorn worker so the in-process queue remains a single worker.

Substack-specific login cookies and image markup workarounds are isolated to the fetch and
extraction paths that need them. Web requests do not require `FEEDS`.

## Configuration and style

`config.py` reads environment variables and `.env` with python-dotenv. `.env.example` documents
CLI settings. Docker Compose reads application settings from the separate, user-created
`.env.runtime` file.

Use type hints, dataclasses for data containers, and Python 3.12 syntax. Ruff is configured in
`pyproject.toml` for a 100 character line length and the E, F, I, W, UP, and B rule sets.
Keep comments focused on why code needs a non-obvious behavior. Prefer small edits, handle
recoverable errors at loop boundaries, and keep Substack-specific logic isolated from general
feed and article handling.
