# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python CLI tool that monitors Substack RSS feeds, fetches full paywalled article content using session cookies, and emails them to a Kindle device as EPUB attachments with embedded images and generated covers.

## Commands

- `uv run rss-to-kindle fetch` — One-shot: check feeds, fetch new articles, send to Kindle
- `uv run rss-to-kindle fetch --latest` — Only fetch the latest article from each feed
- `uv run rss-to-kindle poll` — Continuous: run fetch every POLL_INTERVAL_MINUTES
- `uv pip install -e .` — Install in editable mode after changing pyproject.toml

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

## Development Philosophy

**"Every line of code is a liability"**

- **Prefer editing over adding**: Modify existing functions to be more extensible rather than creating new ones
- **Minimize code**: Write only what's necessary to accomplish the task - no more, no less
- **Prioritize readability**: Code should be easily understood by developers jumping into the codebase.
- **Prudent comments**: Only add comments for things that are not obvious when reading the code, or when summarizing large chunks of code when a comment would greatly improve readability. Comments should generally talk about _why_ the code is doing what it's doing and not just _what_ the code is doing.
- **Favor maintainability**: Simple, clear solutions over clever ones
- **Keep it accessible**: New developers should be able to quickly understand and make changes
