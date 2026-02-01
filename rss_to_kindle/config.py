import json
import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(".env")
ENV_EXAMPLE_PATH = Path(".env.example")


def load_config() -> dict:
    """Load configuration from .env file and environment variables."""
    load_dotenv()

    config = {
        "feeds": _parse_feeds(os.getenv("FEEDS", "")),
        "substack_session_cookie": os.getenv("SUBSTACK_SESSION_COOKIE", ""),
        "substack_connect_cookies": _parse_connect_cookies(),
        "kindle_email": os.getenv("KINDLE_EMAIL", ""),
        "sender_email": os.getenv("SENDER_EMAIL", ""),
        "sender_password": os.getenv("SENDER_PASSWORD", ""),
        "smtp_host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.getenv("SMTP_PORT", "587")),
        "poll_interval_minutes": int(os.getenv("POLL_INTERVAL_MINUTES", "30")),
    }

    _validate(config)
    return config


def _parse_feeds(raw: str) -> list[str]:
    """Parse comma-separated feed URLs."""
    if not raw.strip():
        return []
    return [url.strip() for url in raw.split(",") if url.strip()]


def _parse_connect_cookies() -> dict[str, str]:
    """Parse SUBSTACK_CONNECT_COOKIES JSON env var into a {domain: cookie} dict."""
    raw = os.getenv("SUBSTACK_CONNECT_COOKIES", "")
    if not raw.strip():
        return {}
    try:
        cookies = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(cookies, dict):
        return {k: v for k, v in cookies.items() if isinstance(k, str) and isinstance(v, str)}
    return {}


def load_substack_cookies() -> tuple[str, dict[str, str]]:
    """Load just the Substack cookie config without full validation."""
    load_dotenv()
    return os.getenv("SUBSTACK_SESSION_COOKIE", ""), _parse_connect_cookies()


def _validate(config: dict) -> None:
    """Validate that all required config fields are present."""
    required = [
        ("feeds", "FEEDS"),
        ("kindle_email", "KINDLE_EMAIL"),
        ("sender_email", "SENDER_EMAIL"),
        ("sender_password", "SENDER_PASSWORD"),
    ]
    missing = [env_name for key, env_name in required if not config.get(key)]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


def load_feeds() -> list[str]:
    """Load just the feed URLs from .env without full config validation."""
    load_dotenv()
    return _parse_feeds(os.getenv("FEEDS", ""))


def add_feed_to_env(feed_url: str) -> None:
    """Append a feed URL to the feeds list in the .env file."""
    key = "FEEDS"

    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                existing = line[len(f"{key}=") :]
                lines[i] = (
                    f"{key}={existing},{feed_url}" if existing.strip() else f"{key}={feed_url}"
                )
                break
        else:
            lines.append(f"{key}={feed_url}")
        ENV_PATH.write_text("\n".join(lines) + "\n")
    else:
        ENV_PATH.write_text(f"{key}={feed_url}\n")


def save_kindle_email_to_env(email: str) -> None:
    """Write or update KINDLE_EMAIL in the .env file."""
    key = "KINDLE_EMAIL"
    new_line = f"{key}={email}"

    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = new_line
                break
        else:
            lines.append(new_line)
        ENV_PATH.write_text("\n".join(lines) + "\n")
    else:
        if ENV_EXAMPLE_PATH.exists():
            text = ENV_EXAMPLE_PATH.read_text()
            text = text.replace(f"{key}=name@kindle.com", new_line)
            ENV_PATH.write_text(text)
        else:
            ENV_PATH.write_text(new_line + "\n")


def remove_feed_from_env(feed_url: str) -> None:
    """Remove a feed URL from the feeds list in the .env file."""
    key = "FEEDS"

    if not ENV_PATH.exists():
        raise ValueError(f"Feed not found: {feed_url}")

    lines = ENV_PATH.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            existing = line[len(f"{key}=") :]
            feeds = [u.strip() for u in existing.split(",") if u.strip()]
            if feed_url not in feeds:
                raise ValueError(f"Feed not found: {feed_url}")
            feeds.remove(feed_url)
            lines[i] = f"{key}={','.join(feeds)}"
            break
    else:
        raise ValueError(f"Feed not found: {feed_url}")

    ENV_PATH.write_text("\n".join(lines) + "\n")


def save_cookie_to_env(cookie_value: str, key: str = "SUBSTACK_SESSION_COOKIE") -> None:
    """Write or update a cookie value in the .env file."""
    new_line = f"{key}={cookie_value}"

    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = new_line
                break
        else:
            lines.append(new_line)
        ENV_PATH.write_text("\n".join(lines) + "\n")
    else:
        # Seed from .env.example if available, otherwise create minimal file
        if ENV_EXAMPLE_PATH.exists():
            text = ENV_EXAMPLE_PATH.read_text()
            text = text.replace(f"{key}=s%3A...", new_line)
            ENV_PATH.write_text(text)
        else:
            ENV_PATH.write_text(new_line + "\n")


def save_connect_cookies_to_env(cookies: dict[str, str]) -> None:
    """Write or update SUBSTACK_CONNECT_COOKIES as JSON in the .env file."""
    key = "SUBSTACK_CONNECT_COOKIES"
    new_line = f"{key}={json.dumps(cookies)}"

    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = new_line
                break
        else:
            lines.append(new_line)
        ENV_PATH.write_text("\n".join(lines) + "\n")
    else:
        ENV_PATH.write_text(new_line + "\n")
