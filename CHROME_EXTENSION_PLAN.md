# Chrome Extension Conversion Plan

This document outlines a plan to convert rss-to-kindle from a Python CLI tool to a Chrome browser extension backed by a small centrally-hosted server for ~5 users.

## Current Architecture

```
CLI (click) → feed.py → fetcher.py → extractor.py → kindle.py
                                                        │
                                                   SMTP → Kindle
```

The pipeline is linear and stateless: parse RSS feeds, fetch article HTML, extract clean content + images, build EPUB, email to Kindle. Config lives in `.env`, sent-article state in `~/.rss-to-kindle/sent.json`. GitHub Actions already runs this pipeline on a schedule every 6 hours.

## Why a Hybrid Architecture

A pure extension approach would move the entire pipeline into a background service worker. The problem: Manifest V3 service workers only run while Chrome is open, and they terminate after ~30 seconds of inactivity. Polling for new articles requires the browser to be running 24/7.

A hybrid architecture keeps a server doing the heavy lifting (polling, fetching, building EPUBs, emailing) while the extension provides a proper UI and — critically — seamless cookie forwarding so the server can access paywalled Substack content.

## Hybrid Architecture

```
┌──────────────────────────────────┐         ┌─────────────────────────────┐
│        Chrome Extension          │         │     Server (single VPS)     │
│                                  │         │                             │
│  Options Page                    │         │  API Layer (FastAPI)        │
│    - Kindle email                │────────►│    - PUT /config            │
│    - Feed management             │ config  │    - POST /cookies          │
│    - Polling interval            │  sync   │    - GET /history           │
│                                  │         │    - POST /fetch-now        │
│  Popup                           │         │                             │
│    - Feed status                 │◄────────│  Pipeline (existing Python) │
│    - Recent sends                │ history │    - feed.py                │
│    - "Fetch Now" button          │         │    - fetcher.py             │
│                                  │         │    - extractor.py           │
│  Cookie Forwarder                │         │    - kindle.py              │
│    - Watch substack.sid          │────────►│                             │
│    - Watch connect.sid           │ cookies │  Scheduler (asyncio loop)   │
│    - Warn on expiry              │         │    - Periodic polling       │
│                                  │         │                             │
│  Notifications listener          │◄────────│  SQLite database            │
│    - Article sent                │ events  │    - User config            │
│    - Errors / cookie warnings    │         │    - Cookies (encrypted)    │
│                                  │         │    - Sent article state     │
└──────────────────────────────────┘         └─────────────────────────────┘
```

### What the Extension Does

1. **Configuration UI** — Options page for managing feeds, Kindle email, and polling interval. Syncs to the server via API calls.
2. **Cookie forwarding** — Watches Substack cookies via `chrome.cookies.onChanged` and pushes them to the server whenever they change. This replaces the CLI's `substack-login --from-browser` command with a fully automatic flow.
3. **Status and history** — Popup shows feed status, recently sent articles, and errors, all pulled from the server API.
4. **Manual trigger** — "Fetch Now" button in the popup triggers an immediate server-side pipeline run.
5. **Notifications** — Alerts when articles are sent or when cookies are expiring.

### What the Server Does

1. **Runs the existing Python pipeline** on a schedule — the same `feed.py → fetcher.py → extractor.py → kindle.py` chain, largely unchanged.
2. **Stores config, cookies, and state** in SQLite instead of `.env` and `sent.json` files.
3. **Exposes a small API** for the extension to read/write config, push cookies, view history, and trigger fetches.

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
- No `identity` or `oauth2` needed — authentication is a simple API key (see below).

### Authentication

With ~5 users, Google OAuth is unnecessary overhead (consent screen review, JWT refresh logic, token verification). Instead, use simple API keys:

1. You generate an API key per user on the server (a random token, e.g., `secrets.token_urlsafe(32)`).
2. You give each user their key out-of-band (email, message, etc.).
3. The user pastes it into the extension's Options page.
4. The extension stores it in `chrome.storage.local` and sends it as a `Bearer` token in the `Authorization` header on every API request.

The server validates the key by looking it up in the `users` table. No OAuth libraries, no Google API registration, no consent screen review.

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
- List of feeds with last-checked timestamp
- Recent sends (last 5 articles: title, feed, time)
- "Fetch Now" button
- Cookie status indicator (valid / expiring soon / missing)
- Link to Options page

**Options page**:
- API key input (paste key from server admin)
- Server URL (defaults to the hosted server, configurable)
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
| `init` | First-run: paste API key → enter Kindle email → add feeds |
| `list` | Options page — feed list section |
| `add <url>` | Options page form → `PUT /config` |
| `remove <url>` | Options page delete button → `PUT /config` |
| `history` | Popup recent list + Options page full history → `GET /history` |
| `preview` | Could add as a popup action that downloads EPUB from server |
| `substack-login` | Automatic — cookie forwarding replaces manual import |

## Server Design

### What Changes from the Current Codebase

The existing Python pipeline (`feed.py`, `fetcher.py`, `extractor.py`, `kindle.py`) stays almost unchanged. The main changes are:

1. **config.py** → Read config from SQLite instead of `.env`
2. **state.py** → Read/write sent state from SQLite instead of `sent.json`
3. **New: api.py** → Small FastAPI app for the extension
4. **New: db.py** → SQLite schema and helpers
5. **cli.py** → Kept for admin tasks (creating users, manual fetch), but no longer the primary interface

### Email Sending: Shared Sender

The server sends all emails from a single address (e.g., a Gmail account with an app password, or a simple SES setup) rather than requiring each user to provide SMTP credentials. Users add this sender address to their Kindle's approved sender list during onboarding.

This is the same SMTP approach the CLI already uses — the only difference is all users share one sender. At 5 users and a few articles per day, Gmail's free SMTP limits (500 emails/day) are more than sufficient. No transactional email service needed.

### API Endpoints

```
Authentication: API key as Bearer token in Authorization header

PUT    /config              — Update user config (kindle_email, feeds, interval)
GET    /config              — Get current config
POST   /cookies             — Push Substack cookies from extension
GET    /history             — Get sent article history
POST   /fetch-now           — Trigger immediate pipeline run for this user
GET    /status              — Last fetch time per feed, cookie expiry status
```

### Database Schema

SQLite — a single file, zero configuration, backed up by copying one file.

```sql
CREATE TABLE users (
    id                    INTEGER PRIMARY KEY,
    api_key               TEXT UNIQUE NOT NULL,
    kindle_email          TEXT,
    poll_interval_minutes INTEGER DEFAULT 360,
    created_at            TEXT DEFAULT (datetime('now'))
);

CREATE TABLE feeds (
    id      INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    url     TEXT NOT NULL,
    UNIQUE(user_id, url)
);

CREATE TABLE cookies (
    id      INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    domain  TEXT NOT NULL,
    name    TEXT NOT NULL,
    value   TEXT NOT NULL,          -- encrypted with Fernet
    expires INTEGER,               -- unix timestamp
    UNIQUE(user_id, domain, name)
);

CREATE TABLE sent_articles (
    id        INTEGER PRIMARY KEY,
    user_id   INTEGER REFERENCES users(id) ON DELETE CASCADE,
    url       TEXT NOT NULL,
    title     TEXT,
    author    TEXT,
    feed_url  TEXT,
    sent_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, url)
);
```

### Scheduler

No task queue (Celery, Redis, etc.) needed. A simple `asyncio` background task inside the FastAPI process handles scheduling:

```python
async def scheduler():
    while True:
        users = db.get_all_users()
        for user in users:
            if time_since_last_fetch(user) >= user.poll_interval_minutes:
                await run_pipeline_for_user(user)
        await asyncio.sleep(60)  # check every minute
```

With 5 users, the pipeline runs sequentially and finishes in seconds. No concurrency concerns. `POST /fetch-now` calls `run_pipeline_for_user()` directly.

The FastAPI `lifespan` hook starts this loop on server startup.

### Security Considerations

- **Cookie encryption**: Encrypt Substack cookies at rest with Fernet (symmetric encryption). The key lives in the server's environment or a `.env` file. Same trust model as the current CLI storing cookies in plaintext `.env`, but slightly better.
- **API keys**: Random 32-byte tokens. Validated on every request via a FastAPI dependency.
- **HTTPS**: Run behind a reverse proxy (Caddy is simplest — automatic TLS with Let's Encrypt, zero config). Or use the hosting provider's built-in HTTPS.
- **No user SMTP credentials**: Shared sender means no user passwords stored.

### Infrastructure

Single VPS is all that's needed. The entire stack is one process:

- **Server**: FastAPI + Uvicorn, single process, with the asyncio scheduler running as a background task.
- **Database**: SQLite file on disk. Back up by copying the file (cron job to a cloud bucket, or just `scp`).
- **HTTPS**: Caddy as reverse proxy (auto-TLS), or use a platform like Fly.io / Railway that handles TLS.
- **Email**: Gmail SMTP with an app password, same as the current CLI. Upgrade to SES only if Gmail limits become a problem (they won't at 5 users).

Total cost: $5/month for a small VPS, or free-tier on Fly.io / Railway.

### Admin CLI

Keep a minimal CLI for server administration:

```bash
# Create a new user and print their API key
uv run rss-to-kindle-server add-user

# List all users
uv run rss-to-kindle-server list-users

# Manually trigger a fetch for a user
uv run rss-to-kindle-server fetch --user <id>

# Delete a user
uv run rss-to-kindle-server delete-user <id>
```

## Implementation Phases

### Phase 1 — Server: Database + API
- Set up SQLite schema and helper functions in `db.py`.
- Port `config.py` and `state.py` to read/write from SQLite.
- Create FastAPI app in `api.py` with the endpoints listed above.
- API key validation as a FastAPI dependency.
- Admin CLI for creating users.
- Verify the existing pipeline works with database-backed config.

### Phase 2 — Server: Scheduler + Email
- Add asyncio scheduler as a FastAPI lifespan background task.
- Configure shared sender Gmail SMTP (single set of credentials in server `.env`).
- `POST /fetch-now` triggers immediate pipeline run.
- Basic logging per fetch run.

### Phase 3 — Extension: Scaffold + Config
- Set up Manifest V3 project (TypeScript + Vite or plain JS if simpler).
- Build Options page: API key input, server URL, Kindle email, feed management.
- Build Popup: status, recent sends, "Fetch Now" button.
- Wire up API calls to the server.

### Phase 4 — Extension: Cookie Forwarding
- Implement `chrome.cookies.onChanged` listener for `substack.sid` and `connect.sid`.
- Push cookies to server via `POST /cookies` on change.
- Initial cookie sync on extension install/startup.
- Cookie expiry monitoring with `chrome.alarms` + `chrome.notifications`.

### Phase 5 — Polish
- First-run onboarding flow (paste API key → enter Kindle email → add feeds).
- Error handling for API calls (server down, invalid key, etc.).
- Cookie status indicators in popup.
- Extension icon badge for errors.

## Key Advantages

1. **Zero setup for users** — Install extension, paste API key, configure feeds. Done.
2. **24/7 polling** — Server fetches on schedule regardless of whether Chrome is open.
3. **Seamless cookie sync** — Extension automatically pushes Substack cookies, replacing manual `substack-login --from-browser`.
4. **Minimal new code** — Existing pipeline is unchanged. New code is a small FastAPI app, SQLite helpers, and the extension UI.
5. **Simple to operate** — Single process, single SQLite file, no Redis/Celery/Postgres. Back up by copying one file.

## Key Risks

1. **Cookie trust**: Users are sending their Substack session cookies to your server. Since these are people you know, this is likely acceptable — but be transparent about it.
2. **Chrome Web Store review**: The extension needs `cookies` permission + `host_permissions`, which gets extra scrutiny. Clear justification in the listing description helps. Alternatively, distribute the extension as an unpacked extension (loaded via developer mode) to skip the store entirely — acceptable for 5 users.
3. **Single point of failure**: One VPS, one process. If it goes down, polling stops. Mitigate with a simple health check / uptime monitor that alerts you.
4. **Gmail SMTP limits**: 500 emails/day free tier. At 5 users with ~5-10 articles each per day, you're well under this. Only a risk if usage patterns change significantly.
