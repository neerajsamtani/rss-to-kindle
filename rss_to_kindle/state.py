import json
from pathlib import Path

STATE_DIR = Path.home() / ".rss-to-kindle"
STATE_FILE = STATE_DIR / "sent.json"


def _load_sent() -> set[str]:
    """Load the set of already-sent article URLs."""
    if not STATE_FILE.exists():
        return set()
    data = json.loads(STATE_FILE.read_text())
    return set(data)


def _save_sent(sent: set[str]) -> None:
    """Persist the set of sent article URLs."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(sent), indent=2))


def is_sent(url: str) -> bool:
    """Check if an article URL has already been sent."""
    return url in _load_sent()


def mark_sent(url: str) -> None:
    """Mark an article URL as sent."""
    sent = _load_sent()
    sent.add(url)
    _save_sent(sent)
