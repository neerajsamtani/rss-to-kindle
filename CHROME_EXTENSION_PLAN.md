# Chrome Extension Conversion Plan

This document outlines a plan to convert rss-to-kindle from a Python CLI tool to a Chrome browser extension backed by a centrally-hosted server.

## Current Architecture

```
CLI (click) → feed.py → fetcher.py → extractor.py → kindle.py
                                                        │
                                                   SMTP → Kindle
```

The pipeline is linear and stateless: parse RSS feeds, fetch article HTML, extract clean content + images, build EPUB, email to Kindle. Config lives in `.env`, sent-article state in `~/.rss-to-kindle/sent.json`. GitHub Actions already runs this pipeline on a schedule every 6 hours.

## Why a Hybrid Architecture

A pure extension approach would move the entire pipeline into a background service worker. The problem: Manifest V3 service workers only run while Chrome is open, and they terminate after ~30 seconds of inactivity. Polling for new articles requires the browser to be running 24/7.

A hybrid architecture keeps a centrally-hosted server doing the heavy lifting (polling, fetching, building EPUBs, emailing) while the extension provides a proper UI and — critically — seamless cookie forwarding so the server can access paywalled Substack content.

## Hybrid Architecture

```
┌──────────────────────────────────┐         ┌─────────────────────────────┐
│        Chrome Extension          │         │   Centrally-Hosted Server   │
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
│    - Article sent                │ events  │  Database (PostgreSQL)      │
│    - Errors / cookie warnings    │         │    - User accounts          │
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
    "alarms",
    "identity"
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
  "options_page": "options.html",
  "oauth2": {
    "client_id": "<google-client-id>.apps.googleusercontent.com",
    "scopes": ["openid", "email"]
  }
}
```

Notes:
- `cookies` permission + `host_permissions` for `*.substack.com` gives access to `substack.sid` cookies. For custom Substack domains, `optional_host_permissions` lets the user grant access per-domain when they add a feed.
- `alarms` is used for periodic cookie checks and polling the server for status updates.
- `identity` + `oauth2` are for Google Sign-In — users authenticate with the centralized server via their Google account (see Authentication section below).

### Authentication

With a centralized server, users need accounts. Google Sign-In via `chrome.identity` is the natural choice — the extension already lives in Chrome, and the user likely has a Google account (they need Gmail for sending to Kindle anyway).

Flow:
1. User installs extension, clicks "Sign In with Google" on the Options page.
2. Extension calls `chrome.identity.getAuthToken()` to get a Google OAuth token.
3. Extension sends the token to `POST /auth/google` on the server.
4. Server verifies the token with Google, creates or retrieves the user account, and returns a session token (JWT).
5. Extension stores the JWT in `chrome.storage.local` and includes it in all subsequent API requests.

This eliminates manual API key generation. No passwords, no setup friction — just one click.

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
- Account status (signed in as user@gmail.com)
- List of feeds with last-checked timestamp
- Recent sends (last 5 articles: title, feed, time)
- "Fetch Now" button
- Cookie status indicator (valid / expiring soon / missing)
- Link to Options page

**Options page**:
- Account: Google Sign-In button (or signed-in state with sign-out option)
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
| `init` | First-run: Google Sign-In → Kindle email prompt → feed setup |
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
4. **New: auth.py** → Google OAuth token verification + JWT session management
5. **New: db.py** → Database models and queries
6. **New: scheduler.py** → Per-user polling scheduler (replaces cron / GitHub Actions)
7. **cli.py** → Kept for admin/debugging, but no longer the primary interface

### Email Sending: Server-Managed SMTP

With a centralized server, there are two options for SMTP:

**Option A — Shared sender (recommended)**:
- The server sends all emails from a single service address (e.g., `noreply@rsstokindle.com`) using a transactional email service (SES, Postmark, Sendgrid, etc.).
- Users add this address to their Kindle's approved sender list during onboarding.
- Users never provide SMTP credentials. Simpler setup, no credential storage per user.
- The server pays for email sending, but volume is low (a few emails per user per day).

**Option B — User-provided SMTP**:
- Users provide their own Gmail app password, like the current CLI.
- Server stores and uses their credentials. More complex, more credential risk.

Option A is strongly preferred. It removes an entire category of sensitive data (user SMTP passwords) and simplifies onboarding to: sign in → enter Kindle email → approve sender → add feeds.

### API Endpoints

```
Authentication: JWT bearer token (obtained via Google Sign-In flow)

POST   /auth/google        — Exchange Google OAuth token for JWT session
POST   /config              — Update user config (kindle_email, feeds, interval)
GET    /config              — Get current config
POST   /cookies             — Push Substack cookies from extension
GET    /history             — Get sent article history (with pagination)
POST   /fetch-now           — Trigger immediate pipeline run
GET    /status              — Server health + last fetch time per feed
DELETE /account              — Delete account and all associated data
```

### Database Schema

```sql
-- User accounts (replaces .env, one row per user)
CREATE TABLE users (
    id                    SERIAL PRIMARY KEY,
    google_id             TEXT UNIQUE NOT NULL,
    email                 TEXT NOT NULL,
    kindle_email          TEXT,
    poll_interval_minutes INTEGER DEFAULT 360,
    created_at            TIMESTAMPTZ DEFAULT NOW()
);

-- Feed URLs (replaces FEEDS env var)
CREATE TABLE feeds (
    id      SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    url     TEXT NOT NULL,
    UNIQUE(user_id, url)
);

-- Substack cookies (replaces SUBSTACK_SESSION_COOKIE / SUBSTACK_CONNECT_COOKIES)
CREATE TABLE cookies (
    id      SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    domain  TEXT NOT NULL,
    name    TEXT NOT NULL,
    value   TEXT NOT NULL,          -- encrypted at rest
    expires BIGINT,                 -- unix timestamp
    UNIQUE(user_id, domain, name)
);

-- Sent articles (replaces sent.json)
CREATE TABLE sent_articles (
    id        SERIAL PRIMARY KEY,
    user_id   INTEGER REFERENCES users(id) ON DELETE CASCADE,
    url       TEXT NOT NULL,
    title     TEXT,
    author    TEXT,
    feed_url  TEXT,
    sent_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, url)
);
```

PostgreSQL is the right choice for a centrally-hosted multi-user service. `ON DELETE CASCADE` ensures clean account deletion.

### Scheduler

Use a task queue (Celery with Redis, or a lighter option like `arq` or `huey`) rather than an in-process loop. Benefits for a hosted service:
- Worker processes are separate from the API server — a slow fetch doesn't block API responses.
- Celery beat (or equivalent) handles per-user periodic schedules.
- Failed tasks get retried automatically.
- Scales horizontally by adding more workers.

On each tick per user:
1. Load user's config and cookies from database
2. Run `_process_feeds()` with those values
3. Write results to database
4. Send email via the shared sender

`POST /fetch-now` enqueues an immediate task for the user.

### Rate Limiting and Abuse Prevention

A centrally-hosted server is open to abuse. Mitigations:

- **Feed limits**: Cap feeds per user (e.g., 20). Prevents a single user from hammering thousands of RSS endpoints.
- **Fetch rate limiting**: `POST /fetch-now` is rate-limited (e.g., 1 per 5 minutes per user). Scheduled fetches already have natural limits via `poll_interval_minutes`.
- **Email volume**: Cap emails per user per day (e.g., 50). Prevents using the service as a spam relay.
- **Account limits**: One account per Google identity. No anonymous access.
- **Cookie push rate**: Rate-limit `POST /cookies` (e.g., 10 per minute) to prevent abuse of the cookie storage endpoint.

### Security Considerations

- **Cookie storage**: Substack session cookies are sensitive credentials. Encrypt at rest in the database (e.g., Fernet symmetric encryption with a server-managed key, or use PostgreSQL's pgcrypto). Since this is a centrally-hosted service, the server operator has access to the encryption key — this is a trust tradeoff users accept by using a hosted service instead of self-hosting.
- **Authentication**: Google Sign-In via OAuth 2.0. Server verifies tokens with Google's tokeninfo endpoint. Sessions are JWT with short expiry + refresh.
- **No user SMTP credentials**: Using a shared sender (Option A above) means the server never stores user email passwords.
- **HTTPS**: All traffic over HTTPS. HSTS headers enforced.
- **Account deletion**: `DELETE /account` removes all user data (config, cookies, history) via cascading deletes. Required for GDPR compliance.
- **Data minimization**: Only store what's needed. Cookies are encrypted. Sent article history can be pruned after a configurable retention period.
- **Audit logging**: Log authentication events and cookie pushes for security monitoring.

### Infrastructure

A reasonable starting point:
- **API + scheduler**: Single VPS or a small container service (Fly.io, Railway, Render). FastAPI + Uvicorn for the API, Celery or arq for task processing.
- **Database**: Managed PostgreSQL (e.g., Supabase, Neon, or the hosting provider's managed offering).
- **Email**: Transactional email service (Amazon SES is cheapest at scale, Postmark is simplest to set up).
- **Monitoring**: Basic health checks + error alerting (Sentry for exceptions, uptime monitoring for the API).

The entire stack can start on a single $5-10/month VPS. Costs scale linearly with users — each user adds negligible compute (a few HTTP requests and one SMTP send per poll cycle) and small storage (a few KB of config + history rows).

## Implementation Phases

### Phase 1 — Server API Layer + Auth
- Set up FastAPI project with PostgreSQL (via SQLAlchemy or raw asyncpg).
- Implement Google OAuth token verification and JWT session management.
- Implement database models for users, feeds, cookies, and sent articles.
- Port `config.py` and `state.py` to read/write from the database.
- Expose the API endpoints listed above.
- Set up transactional email sending (SES or Postmark) replacing per-user SMTP.
- Verify the existing pipeline works with database-backed config and shared sender.

### Phase 2 — Server Scheduler + Rate Limiting
- Set up task queue (Celery/arq) with periodic per-user schedules.
- `POST /fetch-now` endpoint enqueues an immediate task.
- Implement rate limiting on all endpoints.
- Feed and email volume caps per user.
- Logging and error tracking per fetch run.

### Phase 3 — Extension Scaffold + Auth
- Set up Manifest V3 project structure (TypeScript + bundler).
- Implement Google Sign-In flow via `chrome.identity.getAuthToken()`.
- Build Options page: account status, Kindle email, feed management.
- Build Popup: status, recent sends, "Fetch Now" button.
- Wire up `chrome.storage.local` for JWT persistence.

### Phase 4 — Cookie Forwarding
- Implement `chrome.cookies.onChanged` listener for `substack.sid` and `connect.sid`.
- Push cookies to server via `POST /cookies` on change.
- Initial cookie sync on extension install/startup.
- Cookie expiry monitoring with `chrome.alarms` + `chrome.notifications`.

### Phase 5 — Status, History, and Notifications
- Popup polls `GET /status` and `GET /history` to show feed state and recent sends.
- Options page shows full searchable history.
- Notification listener for server-side events (sent articles, errors, cookie warnings).

### Phase 6 — Polish and Launch
- First-run onboarding flow (sign in → enter Kindle email → approve sender → add feeds).
- Error handling and retry logic for API calls.
- Cookie status indicators in popup (valid / expiring / missing).
- Extension icon badge showing unread count or error state.
- Privacy policy and terms of service (required for Chrome Web Store and Google OAuth).
- Chrome Web Store listing.

## Key Advantages of the Centrally-Hosted Approach

1. **Zero setup for users** — No server to deploy. Install extension, sign in with Google, configure feeds. Done.
2. **24/7 polling** — Server fetches on schedule regardless of whether Chrome is open.
3. **Seamless cookie sync** — Extension automatically pushes Substack cookies to the server, replacing manual `substack-login --from-browser`.
4. **No user SMTP credentials** — Shared sender eliminates the need for users to create Gmail app passwords.
5. **Minimal pipeline changes** — The existing Python pipeline stays almost intact; only config/state storage changes.
6. **No JS EPUB/email complexity** — EPUB generation and SMTP stay in Python on the server.

## Key Risks

1. **Operational burden**: Running a hosted service means uptime expectations, monitoring, incident response, and ongoing infrastructure costs. Even a small service needs someone on call.
2. **Cookie trust**: Users are sending their Substack session cookies to a third-party server. Some users will not be comfortable with this. Transparent privacy policy, encryption at rest, and clear data deletion options help, but the fundamental trust requirement remains.
3. **Google OAuth consent screen review**: Publishing an app that uses Google Sign-In requires Google's verification process. This can take days to weeks. Start early.
4. **Chrome Web Store review**: The extension needs broad cookie access (`cookies` permission + `host_permissions`), which receives extra scrutiny during Chrome Web Store review. Clear justification in the listing description is important.
5. **Email deliverability**: A shared sender address sending EPUB attachments to Kindle could trigger spam filters if volume grows. Dedicated IP, proper SPF/DKIM/DMARC, and a reputable email provider mitigate this.
6. **Cost scaling**: Each user adds marginal cost (compute, email, storage). At scale, the transactional email service becomes the largest cost. May need a paid tier if user count grows significantly.
7. **Substack blocking**: If many users' requests originate from the same server IP, Substack might rate-limit or block it. Mitigate with respectful polling intervals and consider rotating IPs if needed.
