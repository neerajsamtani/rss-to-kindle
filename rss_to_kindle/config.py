import os

from dotenv import load_dotenv


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
