import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(".env")
ENV_EXAMPLE_PATH = Path(".env.example")


def load_config() -> dict:
    """Load configuration from .env file and environment variables."""
    load_dotenv()

    config = {
        "substack_feeds": _parse_feeds(os.getenv("SUBSTACK_FEEDS", "")),
        "substack_session_cookie": os.getenv("SUBSTACK_SESSION_COOKIE", ""),
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


def _validate(config: dict) -> None:
    """Validate that all required config fields are present."""
    required = [
        ("substack_feeds", "SUBSTACK_FEEDS"),
        ("substack_session_cookie", "SUBSTACK_SESSION_COOKIE"),
        ("kindle_email", "KINDLE_EMAIL"),
        ("sender_email", "SENDER_EMAIL"),
        ("sender_password", "SENDER_PASSWORD"),
    ]
    missing = [env_name for key, env_name in required if not config.get(key)]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )


def save_cookie_to_env(cookie_value: str) -> None:
    """Write or update SUBSTACK_SESSION_COOKIE in the .env file."""
    key = "SUBSTACK_SESSION_COOKIE"
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
