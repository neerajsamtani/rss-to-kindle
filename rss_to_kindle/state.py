import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

STATE_DIR = Path.home() / ".rss-to-kindle"
STATE_FILE = STATE_DIR / "sent.json"


@dataclass
class SentRecord:
    url: str
    title: str | None = None
    author: str | None = None
    sent_at: str | None = None
    feed_url: str | None = None


def _load_sent() -> dict[str, dict]:
    """Load the dict of already-sent article records."""
    if not STATE_FILE.exists():
        return {}
    data = json.loads(STATE_FILE.read_text())
    return data


def _save_sent(sent: dict[str, dict]) -> None:
    """Persist the dict of sent article records."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sent, indent=2))


def is_sent(url: str) -> bool:
    """Check if an article URL has already been sent."""
    return url in _load_sent()


def mark_sent(
    url: str,
    *,
    title: str | None = None,
    author: str | None = None,
    feed_url: str | None = None,
) -> None:
    """Mark an article URL as sent with optional metadata."""
    sent = _load_sent()
    sent[url] = {
        "title": title,
        "author": author,
        "sent_at": datetime.now(UTC).isoformat(),
        "feed_url": feed_url,
    }
    _save_sent(sent)


def get_history() -> list[SentRecord]:
    """Load all sent records, sorted by sent_at descending (newest first)."""
    sent = _load_sent()
    records = [SentRecord(url=url, **meta) for url, meta in sent.items()]
    records.sort(key=lambda r: r.sent_at or "", reverse=True)
    return records
