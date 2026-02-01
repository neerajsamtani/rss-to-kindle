# Chrome Extension Conversion Plan

This document outlines a plan to convert rss-to-kindle from a Python CLI tool to a Google Chrome browser extension.

## Current Architecture

```
CLI (click) → feed.py → fetcher.py → extractor.py → kindle.py
                                                        │
                                                   SMTP → Kindle
```

The pipeline is linear and stateless: parse RSS feeds, fetch article HTML, extract clean content + images, build EPUB, email to Kindle. Config lives in `.env`, sent-article state in `~/.rss-to-kindle/sent.json`.

## Extension Architecture

```
Popup UI / Options Page
        │
        ▼
Background Service Worker
   ├── feed.ts         (RSS parsing)
   ├── fetcher.ts      (HTTP fetching)
   ├── extractor.ts    (content extraction + images)
   ├── epub.ts         (EPUB generation)
   └── sender.ts       (email via backend or API)
        │
Chrome Storage (config + sent history)
Chrome Cookies API (Substack auth)
Chrome Alarms API (periodic fetch)
```

## Module-by-Module Migration

### 1. config.py → Chrome Storage API

| Python (current) | Chrome Extension |
|---|---|
| `.env` file via python-dotenv | `chrome.storage.sync` for config that roams across devices |
| `~/.rss-to-kindle/sent.json` | `chrome.storage.local` for sent history (larger quota) |
| Environment variables | Options page form that writes to storage |

The Options page replaces `rss-to-kindle init`. Users fill in Kindle email, SMTP credentials (or API key — see sender section), and feed URLs through a form.

### 2. feed.py → feed.ts

**Current**: `feedparser` library parses RSS/Atom XML and returns `FeedArticle` dataclasses.

**Extension**: Use the browser's `fetch` + `DOMParser` to parse RSS XML directly. RSS and Atom feeds are XML, so `DOMParser` handles them natively. Extract `<item>` / `<entry>` elements and map to a `FeedArticle` TypeScript interface.

**CORS consideration**: RSS feeds are typically served without CORS headers. Options:
- Fetch from the **background service worker** — Manifest V3 service workers can make cross-origin requests if the host is listed in `host_permissions`.
- Add `"host_permissions": ["*://*/"]"` (broad) or let users grant per-feed host permission via `optional_host_permissions`.

Substack detection (`generator` tag check) carries over directly.

### 3. fetcher.py → fetcher.ts

**Current**: `httpx.get()` with optional Substack session cookies.

**Extension**: `fetch()` from the service worker. Substack cookies are handled automatically by the browser — if the user is logged in to Substack, the browser attaches cookies on requests to `*.substack.com` and custom domains. No manual cookie injection needed.

This is a major simplification: `browser-cookie3`, `SUBSTACK_SESSION_COOKIE`, and `SUBSTACK_CONNECT_COOKIES` all become unnecessary. The `chrome.cookies` API can still be used to *check* if cookies exist and warn about expiry, but the browser handles attachment automatically.

### 4. extractor.py → extractor.ts

**Current**: readability-lxml + BeautifulSoup for content extraction, Pillow for image compression.

**Extension**:
- **Content extraction**: [Readability.js](https://github.com/nicholasgm/nicholasgm-readability-js) (Mozilla's library, used by Firefox Reader View). Drop-in replacement for readability-lxml. Runs in the service worker on a parsed DOM.
- **Substack workarounds** (`_simplify_images`, `_simplify_headers`): Port directly — the logic is DOM manipulation that maps 1:1 from BeautifulSoup to standard DOM APIs (`querySelector`, `replaceWith`, etc.).
- **Image downloading**: `fetch()` + `createImageBitmap()` or `OffscreenCanvas` for resizing. Alternatively, use a library like [pica](https://github.com/nicholasgm/nicholasgm-pica) for high-quality image resizing in JS.
- **Image compression**: `OffscreenCanvas.convertToBlob({ type: 'image/jpeg', quality: 0.85 })` replaces Pillow's JPEG compression.

### 5. kindle.py → epub.ts + sender.ts

This is the most complex migration because Chrome extensions cannot open raw SMTP sockets.

#### EPUB Generation

**Current**: `ebooklib` builds the EPUB (cover image via Pillow, article content, embedded images).

**Extension**: Use [JSZip](https://stuk.github.io/jszip/) to assemble the EPUB manually. An EPUB is a ZIP file with a specific structure:
```
mimetype
META-INF/container.xml
OEBPS/content.opf
OEBPS/article.xhtml
OEBPS/cover.xhtml
OEBPS/images/...
```

The cover image can be generated with `OffscreenCanvas` (service workers support this in Manifest V3). Draw title/author text + optional background image, export as JPEG.

#### Email Sending

Three options, in order of preference:

**Option A — Gmail API (recommended)**:
- Use `chrome.identity` to authenticate with OAuth2 (the user already has a Gmail account for SMTP).
- Send emails via the [Gmail API](https://developers.google.com/gmail/api/reference/rest/v1/users.messages/send) — construct a MIME message with the EPUB attachment, base64url-encode it, POST to the API.
- No backend server needed. Stays entirely client-side.
- Permissions: `https://www.googleapis.com/auth/gmail.send` scope.

**Option B — Backend relay service**:
- Deploy a small server (e.g., Cloudflare Worker or AWS Lambda) that accepts EPUB bytes + Kindle email and sends via SMTP.
- Extension authenticates to the backend via an API key stored in `chrome.storage.sync`.
- Adds infrastructure overhead but keeps SMTP logic unchanged.

**Option C — Send-to-Kindle API**:
- Amazon's [Send to Kindle](https://www.amazon.com/sendtokindle) service. If Amazon exposes an API or if the extension can automate the web flow via content scripts, this removes email entirely.
- Currently not a public API, so this is fragile.

**Recommendation**: Start with Option A (Gmail API). It requires no backend, the auth flow is native to Chrome extensions, and Gmail is already the default SMTP provider.

### 6. state.py → Chrome Storage

**Current**: JSON file mapping URL → `{title, author, sent_at, feed_url}`.

**Extension**: `chrome.storage.local.get('sent')` / `.set()`. Same data structure, just stored in the browser's local storage instead of a file. Add a periodic cleanup (e.g., drop entries older than 90 days) since `chrome.storage.local` has a 10 MB quota by default (expandable with `"unlimitedStorage"` permission).

### 7. cli.py → UI Layer

The CLI commands map to extension UI elements:

| CLI Command | Extension Equivalent |
|---|---|
| `fetch` | Background service worker triggered by alarm or manual button |
| `poll` | `chrome.alarms.create("fetch", { periodInMinutes: 360 })` |
| `init` | Options page (first-run detection via empty config) |
| `list` | Options page — feed list section |
| `add <url>` | Options page form or popup "Add Feed" input |
| `remove <url>` | Options page — delete button per feed |
| `history` | Popup panel showing sent articles |
| `preview` | Popup action: "Preview" button generates EPUB and triggers download |
| `substack-login` | Unnecessary — browser already has cookies |

**Popup** (browser action click): Shows feed status, recent articles, "Fetch Now" button, link to history.

**Options page**: Full configuration — Kindle email, Gmail OAuth connection, feed management, fetch interval, history viewer.

**Notifications**: `chrome.notifications.create()` to alert when articles are sent or errors occur.

## Manifest V3 Permissions

```json
{
  "manifest_version": 3,
  "permissions": [
    "storage",
    "alarms",
    "notifications",
    "identity",
    "cookies",
    "offscreen"
  ],
  "optional_host_permissions": [
    "*://*/*"
  ],
  "background": {
    "service_worker": "background.js",
    "type": "module"
  }
}
```

Notes:
- `optional_host_permissions` with `*://*/*` lets users add arbitrary feed URLs. The extension requests access per-domain when a feed is added.
- `identity` is for Gmail OAuth.
- `cookies` is for checking Substack session expiry (not for injection — the browser handles that).
- `offscreen` is needed if using `OffscreenCanvas` for cover generation and image compression (service workers in MV3 don't have full Canvas access without an offscreen document).

## Implementation Phases

### Phase 1 — Scaffold and Config
- Set up Manifest V3 project structure (TypeScript, bundler like Vite or webpack).
- Implement Options page with feed management and Kindle email config.
- Wire up `chrome.storage.sync` for config persistence.
- Wire up `chrome.storage.local` for sent history.

### Phase 2 — Feed Parsing and Fetching
- Port `feed.py` logic: fetch RSS XML via `fetch()`, parse with `DOMParser`, return typed article objects.
- Port `fetcher.py`: fetch article HTML from service worker (cookies attached automatically).
- Implement Substack detection and paywall warning.

### Phase 3 — Content Extraction
- Integrate Readability.js for content extraction.
- Port `_simplify_images()` and `_simplify_headers()` Substack workarounds using DOM APIs.
- Implement image downloading and compression via `OffscreenCanvas` / offscreen document.

### Phase 4 — EPUB Generation
- Build EPUB assembly with JSZip (mimetype, OPF manifest, XHTML content, embedded images).
- Port cover generation to `OffscreenCanvas` (text rendering, gradient overlay, og:image background).
- Implement `preview` as a local download action.

### Phase 5 — Email Delivery
- Implement Gmail OAuth flow via `chrome.identity.getAuthToken()`.
- Build MIME message construction (multipart with EPUB attachment).
- Send via Gmail API `users.messages.send`.
- Mark articles as sent in storage.

### Phase 6 — Scheduling and Notifications
- Set up `chrome.alarms` for periodic fetching.
- Add `chrome.notifications` for send confirmations and errors.
- Handle service worker lifecycle (MV3 service workers are ephemeral — alarm handler re-triggers the pipeline).

### Phase 7 — Polish
- First-run onboarding flow (detect empty config, guide through setup).
- Popup UI: feed status, recent sends, manual fetch button.
- Cookie expiry warnings for Substack.
- Error handling and retry logic.
- History cleanup (prune old entries).

## Key Simplifications vs. CLI

1. **No cookie management** — the browser handles Substack auth natively.
2. **No `.env` files** — Chrome storage is simpler and syncs across devices.
3. **No `browser-cookie3`** — direct `chrome.cookies` API access.
4. **No Python dependency chain** — single JS bundle.
5. **No SMTP socket management** — Gmail API handles delivery.

## Key Risks

1. **EPUB in JS**: No mature equivalent to `ebooklib`. Manual EPUB assembly with JSZip is straightforward but needs thorough testing across Kindle devices.
2. **Service worker limits**: MV3 service workers terminate after ~30 seconds of inactivity. Long-running fetches (many feeds, many articles) need to use `chrome.offscreen` or break work into chunks across alarm ticks.
3. **Gmail OAuth review**: Google requires OAuth consent screen verification for apps accessing Gmail. During development, the extension works in "testing" mode (limited to 100 users). Publishing requires Google review.
4. **Image processing**: `OffscreenCanvas` in service workers has varying support. May need an offscreen document as fallback.
5. **Feed CORS**: Some feeds may not work with `fetch()` even from service workers if the server blocks non-browser requests. `host_permissions` should handle most cases, but edge cases may require a CORS proxy.
