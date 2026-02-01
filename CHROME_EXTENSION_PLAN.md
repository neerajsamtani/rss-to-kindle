# Chrome Extension Conversion Plan

This document outlines a plan to convert rss-to-kindle from a Python CLI tool to a Chrome browser extension backed by a centralized server.

## Current Architecture

```
CLI (click) → feed.py → fetcher.py → extractor.py → kindle.py
                                                        │
                                                   SMTP → Kindle
```

The pipeline is linear and stateless: parse RSS feeds, fetch article HTML, extract clean content + images, build EPUB, email to Kindle. Config lives in `.env`, sent-article state in `~/.rss-to-kindle/sent.json`. GitHub Actions already runs this pipeline on a schedule every 6 hours.

## Why a Hybrid Architecture

A pure extension approach would move the entire pipeline into a background service worker. The problem: Manifest V3 service workers only run while Chrome is open, and they terminate after ~30 seconds of inactivity. Polling for new articles requires the browser to be running 24/7.

A hybrid architecture keeps the server doing what it already does (polling, fetching, building EPUBs, emailing) while the extension provides a proper UI and — critically — seamless cookie forwarding so the server can access paywalled Substack content.

## Hybrid Architecture

```
┌──────────────────────────────────┐         ┌─────────────────────────────┐
│        Chrome Extension          │         │     Centralized Server      │
│                                  │         │                             │
│  Options Page                    │         │  API Layer                  │
│    - Kindle email                │────────►│    - POST /config           │
│    - Feed management             │ config  │    - POST /cookies          │
│    - Polling interval            │  sync   │    - GET  /history          │
│                                  │         │    - POST /fetch-now        │
│  Popup                           │         │                             │
│    - Feed status                 │◄────────│  Pipeline (existing Python) │
│    - Recent sends                │ history │    - feed.py                │
│    - "Fetch Now" button          │         │    - fetcher.py             │
│                                  │         │    - extractor.py           │
│  Cookie Forwarder                │         │    - kindle.py              │
│    - Watch substack.sid          │────────►│                             │
│    - Watch connect.sid           │ cookies │  Scheduler                  │
│    - Warn on expiry              │         │    - Periodic polling       │
│                                  │         │    - Per-user intervals     │
│  Notifications listener          │◄────────│                             │
│    - Article sent                │ events  │  Database                   │
│    - Errors / cookie warnings    │         │    - User config            │
│                                  │         │    - Cookies (encrypted)    │
└──────────────────────────────────┘         │    - Sent article state     │
                                             └─────────────────────────────┘
```

### What the Extension Does

1. **Configuration UI** — Options page for managing feeds, Kindle email, and polling interval. Syncs to the server via API calls.
2. **Cookie forwarding** — Watches Substack cookies via `chrome.cookies.onChanged` and pushes them to the server whenever they change. This replaces the CLI's `substack-login --from-browser` command with a fully automatic flow.
3. **Status and history** — Popup shows feed status, recently sent articles, and errors, all pulled from the server API.
4. **Manual trigger** — "Fetch Now" button in the popup triggers an immediate server-side pipeline run.
5. **Notifications** — Alerts when articles are sent or when cookies are expiring.

### What the Server Does

1. **Runs the existing Python pipeline** on a schedule — the same `feed.py → fetcher.py → extractor.py → kindle.py` chain, largely unchanged.
2. **Stores config, cookies, and state** in a database instead of `.env` and `sent.json` files.
3. **Exposes an API** for the extension to read/write config, push cookies, view history, and trigger fetches.
4. **Sends notifications** to the extension (via polling or WebSocket) when articles are sent or errors occur.

### Why Not Proxy Requests Through the Extension?

An alternative is having the server ask the extension to fetch paywalled pages (since the browser has the cookies natively). This avoids storing cookies server-side but means paywalled content can only be fetched while Chrome is open — defeating the purpose of a centralized server. Pushing cookies to the server keeps it fully autonomous.

## Extension Design

### Manifest V3 Configuration

```json
{
  "manifest_version": 3,
  "name": "RSS to Kindle",
  "permissions": [
    "storage",
    "cookies",
    "notifications",
    "alarms"
  ],
  "host_permissions": [
    "*://*.substack.com/*"
  ],
  "optional_host_permissions": [
    "*://*/*"
  ],
  "background": {
    "service_worker": "background.js",
    "type": "module"
  },
  "action": {
    "default_popup": "popup.html"
  },
  "options_page": "options.html"
}
```

Notes:
- `cookies` permission + `host_permissions` for `*.substack.com` gives access to `substack.sid` cookies. For custom Substack domains, `optional_host_permissions` lets the user grant access per-domain when they add a feed.
- `alarms` is used for periodic cookie checks and polling the server for status updates.
- No `identity` permission needed — auth is handled server-side, not via Gmail OAuth.

### Cookie Forwarding

This is the extension's most important job. The flow:

1. **On install / startup**: Read all `substack.sid` cookies from `.substack.com` and `connect.sid` cookies from any configured custom domains via `chrome.cookies.getAll()`. Push to server.
2. **On change**: Listen to `chrome.cookies.onChanged` filtered to `substack.sid` and `connect.sid`. When a cookie changes (user logs in, cookie refreshes), push the new value + expiry to the server immediately.
3. **Expiry monitoring**: `chrome.alarms` fires periodically (e.g., daily) to check cookie expiry dates. If any cookie is within 7 days of expiring, show a `chrome.notifications` warning — matching the CLI's existing behavior.

```typescript
// background.ts — cookie forwarding sketch
chrome.cookies.onChanged.addListener(({ cookie, removed }) => {
  if (removed) return;
  if (cookie.name === "substack.sid" || cookie.name === "connect.sid") {
    pushCookieToServer(cookie.domain, cookie.name, cookie.value, cookie.expirationDate);
  }
});
```

### UI Screens

**Popup** (click extension icon):
- Server connection status (connected / last sync time)
- List of feeds with last-checked timestamp
- Recent sends (last 5 articles: title, feed, time)
- "Fetch Now" button
- Cookie status indicator (valid / expiring soon / missing)
- Link to Options page

**Options page**:
- Server URL + API key (for authenticating with the server)
- Kindle email
- Feed management (add / remove URLs, shows Substack detection status)
- Polling interval selector
- Cookie status details (per-domain expiry dates)
- Full sent history with search

### CLI Command Mapping

| CLI Command | Extension Equivalent |
|---|---|
| `fetch` | "Fetch Now" button → `POST /fetch-now` to server |
| `poll` | Server-side scheduler (configurable interval via Options) |
| `init` | First-run detection: if no server URL configured, show setup flow |
| `list` | Options page — feed list section |
| `add <url>` | Options page form or popup input → `POST /config/feeds` |
| `remove <url>` | Options page delete button → `DELETE /config/feeds/:url` |
| `history` | Popup recent list + Options page full history → `GET /history` |
| `preview` | Could add as a popup action that downloads EPUB from server |
| `substack-login` | Automatic — cookie forwarding replaces manual import |

## Server Design

### What Changes from the Current Codebase

The existing Python pipeline (`feed.py`, `fetcher.py`, `extractor.py`, `kindle.py`) stays almost unchanged. The main changes are:

1. **config.py** → Read config from database instead of `.env`
2. **state.py** → Read/write sent state from database instead of `sent.json`
3. **New: api.py** → HTTP API layer for the extension
4. **New: db.py** → Database models and queries
5. **New: scheduler.py** → Per-user polling scheduler (replaces cron / GitHub Actions)
6. **cli.py** → Kept for direct server-side use, but no longer the primary interface

### API Endpoints

```
Authentication: API key per user (generated on first setup)

POST   /config              — Update user config (kindle_email, feeds, interval)
GET    /config              — Get current config
POST   /cookies             — Push Substack cookies from extension
GET    /history             — Get sent article history (with pagination)
POST   /fetch-now           — Trigger immediate pipeline run
GET    /status              — Server health + last fetch time per feed
```

### Database Schema

```sql
-- User config (replaces .env)
CREATE TABLE users (
    id            TEXT PRIMARY KEY,  -- API key
    kindle_email  TEXT NOT NULL,
    sender_email  TEXT NOT NULL,
    sender_password TEXT NOT NULL,   -- encrypted
    smtp_host     TEXT DEFAULT 'smtp.gmail.com',
    smtp_port     INTEGER DEFAULT 587,
    poll_interval_minutes INTEGER DEFAULT 360
);

-- Feed URLs (replaces FEEDS env var)
CREATE TABLE feeds (
    id      INTEGER PRIMARY KEY,
    user_id TEXT REFERENCES users(id),
    url     TEXT NOT NULL,
    UNIQUE(user_id, url)
);

-- Substack cookies (replaces SUBSTACK_SESSION_COOKIE / SUBSTACK_CONNECT_COOKIES)
CREATE TABLE cookies (
    id      INTEGER PRIMARY KEY,
    user_id TEXT REFERENCES users(id),
    domain  TEXT NOT NULL,
    name    TEXT NOT NULL,
    value   TEXT NOT NULL,          -- encrypted
    expires INTEGER,               -- unix timestamp
    UNIQUE(user_id, domain, name)
);

-- Sent articles (replaces sent.json)
CREATE TABLE sent_articles (
    id        INTEGER PRIMARY KEY,
    user_id   TEXT REFERENCES users(id),
    url       TEXT NOT NULL,
    title     TEXT,
    author    TEXT,
    feed_url  TEXT,
    sent_at   TEXT NOT NULL,       -- ISO 8601
    UNIQUE(user_id, url)
);
```

A lightweight option like SQLite works for single-server deployment. For multi-user scale, PostgreSQL.

### Scheduler

Replace GitHub Actions cron with an in-process scheduler (e.g., APScheduler or a simple asyncio loop). Each user has their own polling interval. On each tick:

1. Load user's config and cookies from database
2. Run `_process_feeds()` with those values (the existing pipeline function)
3. Write results to database
4. Optionally notify the extension via a status endpoint

### Security Considerations

- **Cookie storage**: Substack session cookies are sensitive credentials. Encrypt at rest in the database (e.g., Fernet symmetric encryption with a server-side key). The CLI already stores them in plaintext in `.env`, so this is an improvement.
- **API authentication**: Each user gets an API key. The extension stores it in `chrome.storage.local`. All API requests include it in an `Authorization` header.
- **SMTP credentials**: Encrypted in the database, same as cookies.
- **HTTPS**: All extension ↔ server communication over HTTPS.
- **Self-hosted by default**: The server is designed to be self-hosted (like the current CLI), avoiding third-party trust. A hosted option could come later.

## Implementation Phases

### Phase 1 — Server API Layer
- Add a lightweight HTTP framework (FastAPI or Flask) alongside the existing CLI.
- Implement database models (SQLite initially) for users, feeds, cookies, and sent articles.
- Port `config.py` and `state.py` to read/write from the database.
- Expose the API endpoints listed above.
- Verify the existing pipeline works with database-backed config.

### Phase 2 — Server Scheduler
- Replace GitHub Actions / cron with an in-process scheduler.
- Per-user polling intervals stored in the database.
- `POST /fetch-now` endpoint triggers an immediate run.
- Logging and error tracking per fetch run.

### Phase 3 — Extension Scaffold
- Set up Manifest V3 project structure (TypeScript + bundler).
- Build Options page: server URL, API key, Kindle email, feed management.
- Build Popup: status, recent sends, "Fetch Now" button.
- Wire up `chrome.storage.local` for API key persistence.

### Phase 4 — Cookie Forwarding
- Implement `chrome.cookies.onChanged` listener for `substack.sid` and `connect.sid`.
- Push cookies to server via `POST /cookies` on change.
- Initial cookie sync on extension install/startup.
- Cookie expiry monitoring with `chrome.alarms` + `chrome.notifications`.

### Phase 5 — Status and History
- Popup polls `GET /status` and `GET /history` to show feed state and recent sends.
- Options page shows full searchable history.
- Notification listener for server-side events (sent articles, errors, cookie warnings).

### Phase 6 — Polish
- First-run onboarding flow in the extension (detect missing server URL, guide setup).
- Error handling and retry logic for API calls.
- Cookie status indicators in popup (valid / expiring / missing).
- Extension icon badge showing unread count or error state.
- Documentation for self-hosting the server.

## Key Advantages of the Hybrid Approach

1. **24/7 polling** — Server fetches on schedule regardless of whether Chrome is open.
2. **Seamless cookie sync** — Extension automatically pushes Substack cookies to the server, replacing manual `substack-login --from-browser`.
3. **Minimal pipeline changes** — The existing Python pipeline stays almost intact; only config/state storage changes.
4. **No JS EPUB/email complexity** — EPUB generation and SMTP stay in Python on the server, avoiding the need for JS ports of `ebooklib` and `smtplib`.
5. **Separation of concerns** — Extension handles UI + cookie access; server handles processing + delivery.

## Key Risks

1. **Self-hosting barrier**: Users need to run a server, which is more complex than a standalone extension. Mitigate with Docker compose and clear docs. A future hosted tier could remove this requirement.
2. **Cookie security**: Storing Substack cookies on a server (even self-hosted) expands the attack surface vs. browser-only storage. Mitigate with encryption at rest and HTTPS.
3. **API key management**: Users need to generate and configure an API key. Keep the setup flow minimal (server generates key, user pastes into extension).
4. **Server framework choice**: Adding FastAPI/Flask increases the dependency surface. Keep the API layer thin — it's just a CRUD wrapper around the existing pipeline.
5. **Multi-device sync**: If a user has Chrome on multiple devices, each extension instance pushes cookies. The server should handle upserts gracefully (last-write-wins is fine for cookies).
