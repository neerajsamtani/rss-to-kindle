# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python CLI tool that monitors Substack RSS feeds, fetches full paywalled article content using session cookies, and emails them to a Kindle device as HTML attachments.

## Commands

- `uv run rss-to-kindle fetch` — One-shot: check feeds, fetch new articles, send to Kindle
- `uv run rss-to-kindle poll` — Continuous: run fetch every POLL_INTERVAL_MINUTES
- `uv pip install -e .` — Install in editable mode after changing pyproject.toml

## Architecture

The pipeline flows linearly: **feed.py → fetcher.py → extractor.py → kindle.py**, orchestrated by **cli.py**.

- `config.py` loads `.env` via python-dotenv and validates required fields
- `feed.py` parses RSS feeds with feedparser, returns `FeedArticle` dataclasses
- `fetcher.py` fetches full article HTML via httpx using the Substack `connect.sid` session cookie
- `extractor.py` uses readability-lxml to extract clean article content from raw HTML
- `kindle.py` wraps content in Kindle-friendly HTML and sends as email attachment via SMTP
- `state.py` tracks sent article URLs in `~/.rss-to-kindle/sent.json` to prevent duplicates
- `cli.py` wires everything together with click; `_process_feeds()` is the main loop body

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
