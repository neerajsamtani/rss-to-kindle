import os
import time
from pathlib import Path

import click
from dotenv import load_dotenv

from .config import (
    add_feed_to_env,
    load_config,
    load_feeds,
    load_substack_cookies,
    remove_feed_from_env,
    save_connect_cookies_to_env,
    save_cookie_to_env,
    save_kindle_email_to_env,
)
from .extractor import extract_article
from .feed import fetch_feed, filter_recent
from .fetcher import fetch_article_html
from .kindle import build_epub, send_to_kindle
from .state import get_history, is_sent, mark_sent


def _process_feeds(config: dict, latest_only: bool = False, days: int = 3) -> int:
    """Check all feeds, fetch new articles, and send to Kindle. Returns count of articles sent."""
    sent_count = 0

    for feed_url in config["feeds"]:
        click.echo(f"Checking feed: {feed_url}")
        try:
            articles = fetch_feed(feed_url)
        except Exception as e:
            click.echo(f"  Error parsing feed: {e}", err=True)
            continue

        articles = filter_recent(articles, days)

        if latest_only:
            articles = articles[:1]

        for feed_article in articles:
            if not feed_article.url:
                continue
            if is_sent(feed_article.url):
                click.echo(f"  Skipping (already sent): {feed_article.title}")
                continue

            click.echo(f"  Fetching: {feed_article.title}")
            try:
                raw_html = fetch_article_html(
                    feed_article.url,
                    config["substack_session_cookie"],
                    is_substack=feed_article.is_substack,
                    connect_cookies=config["substack_connect_cookies"],
                )
            except Exception as e:
                click.echo(f"    Error fetching article: {e}", err=True)
                continue

            if feed_article.is_substack:
                has_cookie = bool(
                    config["substack_session_cookie"] or config["substack_connect_cookies"]
                )
                _check_substack_paywall(raw_html, has_cookie)

            article = extract_article(
                raw_html,
                author=feed_article.author,
                published=feed_article.published,
                session_cookie=config["substack_session_cookie"],
                is_substack=feed_article.is_substack,
                url=feed_article.url,
                connect_cookies=config["substack_connect_cookies"],
            )

            click.echo("    Sending to Kindle...")
            try:
                send_to_kindle(
                    article=article,
                    kindle_email=config["kindle_email"],
                    sender_email=config["sender_email"],
                    sender_password=config["sender_password"],
                    smtp_host=config["smtp_host"],
                    smtp_port=config["smtp_port"],
                )
            except Exception as e:
                click.echo(f"    Error sending email: {e}", err=True)
                continue

            mark_sent(
                feed_article.url,
                title=feed_article.title,
                author=feed_article.author,
                feed_url=feed_url,
            )
            sent_count += 1
            click.echo(f"    Sent: {feed_article.title}")

    return sent_count


@click.group()
def main():
    """Monitor RSS feeds and send articles to Kindle."""
    pass


SUPPORTED_BROWSERS = ["chrome", "firefox", "opera", "edge", "chromium"]


def _import_substack_cookie(browser: str) -> None:
    """Import Substack session cookies from the given browser and save them to .env.

    Substack uses two session cookies: substack.sid on .substack.com and connect.sid
    on each newsletter's custom domain. We collect connect.sid per domain.
    """
    from urllib.parse import urlparse

    import browser_cookie3

    loader = getattr(browser_cookie3, browser)

    # Collect feed domains so we can filter connect.sid cookies to relevant ones
    feed_domains: set[str] = set()
    for feed_url in load_feeds():
        host = urlparse(feed_url).hostname
        if host:
            feed_domains.add(host)

    # Single loader call to avoid repeated keychain/password prompts on macOS
    try:
        jar = loader()
    except Exception as e:
        raise click.ClickException(
            f"Could not read cookies from {browser}: {e}\n"
            "Make sure the browser is closed and try again."
        ) from e

    session_cookie = ""
    connect_cookies: dict[str, str] = {}
    for cookie in jar:
        if cookie.name == "substack.sid" and "substack.com" in cookie.domain:
            session_cookie = cookie.value
        elif cookie.name == "connect.sid":
            cookie_domain = cookie.domain.lstrip(".")
            if cookie_domain in feed_domains:
                connect_cookies[cookie_domain] = cookie.value

    if not session_cookie and not connect_cookies:
        raise click.ClickException(
            f"No Substack session cookie found in {browser}.\n"
            "Log into substack.com in that browser first, then re-run this command."
        )

    found_parts = []
    if session_cookie:
        save_cookie_to_env(session_cookie)
        found_parts.append("SUBSTACK_SESSION_COOKIE")
    if connect_cookies:
        # Merge with any existing connect cookies
        _, existing = load_substack_cookies()
        existing.update(connect_cookies)
        save_connect_cookies_to_env(existing)
        found_parts.append(f"SUBSTACK_CONNECT_COOKIES ({len(existing)} domain(s))")
    click.echo(f"Found Substack login from {browser} ({', '.join(found_parts)}).")


def _detect_substack(raw_html: str) -> bool:
    """Detect whether HTML was served by Substack (works for custom domains too)."""
    return "substackcdn.com" in raw_html


def _check_substack_paywall(raw_html: str, has_cookie: bool) -> bool:
    """Return True if a Substack paywall is detected, and warn the user."""
    if 'class="paywall"' not in raw_html:
        return False
    if has_cookie:
        click.echo(
            "    Warning: Paywall detected — your Substack session cookie may be expired.\n"
            "    Run: rss-to-kindle substack-login --from-browser <browser>",
            err=True,
        )
    else:
        click.echo(
            "    Warning: Paywall detected — no Substack session cookies configured.\n"
            "    Run: rss-to-kindle substack-login --from-browser <browser>",
            err=True,
        )
    return True


@main.command("substack-login")
@click.option(
    "--from-browser",
    required=True,
    type=click.Choice(SUPPORTED_BROWSERS),
    help="Browser to read the Substack session cookie from.",
)
def substack_login(from_browser):
    """Import your Substack session cookie from a browser."""
    _import_substack_cookie(from_browser)


@main.command()
@click.option("--latest", is_flag=True, help="Only fetch the latest article from each feed.")
@click.option(
    "--days", default=3, type=int, help="Only process articles published within this many days."
)
def fetch(latest, days):
    """One-shot: check feeds, fetch & send any new articles."""
    config = load_config()
    sent = _process_feeds(config, latest_only=latest, days=days)
    click.echo(f"Done. Sent {sent} article(s).")


@main.command()
def poll():
    """Continuous: run fetch every POLL_INTERVAL_MINUTES."""
    config = load_config()
    interval = config["poll_interval_minutes"]
    click.echo(f"Polling every {interval} minutes. Press Ctrl+C to stop.")

    while True:
        sent = _process_feeds(config)
        click.echo(f"Cycle complete. Sent {sent} article(s). Sleeping {interval} minutes...")
        try:
            time.sleep(interval * 60)
        except KeyboardInterrupt:
            click.echo("\nStopped.")
            break


@main.command()
@click.option("--limit", default=20, type=int, help="Maximum number of articles to show.")
def history(limit):
    """Show recently sent articles."""
    records = get_history()
    if not records:
        click.echo("No articles sent yet.")
        return

    total = len(records)
    shown = records[:limit]

    for record in shown:
        title = record.title or record.url
        click.echo(f"  {title}")

        parts = []
        if record.author:
            parts.append(f"by {record.author}")
        if record.sent_at:
            parts.append(f"sent {record.sent_at[:10]}")
        if record.feed_url:
            parts.append(record.feed_url)
        if parts:
            click.echo(f"    {' | '.join(parts)}")

    if total > limit:
        click.echo(f"\nShowing {limit} of {total}")


@main.command("list")
def list_feeds():
    """Show configured feed URLs."""
    feeds = load_feeds()
    if not feeds:
        click.echo("No feeds configured. Add one with: rss-to-kindle add <url>")
        return

    for i, url in enumerate(feeds, 1):
        click.echo(f"  {i}. {url}")


@main.command()
@click.argument("url")
def add(url):
    """Add a feed URL to your configuration."""
    feeds = load_feeds()
    if url in feeds:
        click.echo(f"Feed already configured: {url}")
        return

    add_feed_to_env(url)
    load_dotenv(override=True)  # refresh so _import_substack_cookie sees the new feed
    click.echo(f"Added: {url}")

    # Detect Substack feeds and offer to import cookies for paid newsletters
    try:
        articles = fetch_feed(url)
    except Exception:
        return

    if not articles or not articles[0].is_substack:
        return

    from urllib.parse import urlparse

    hostname = urlparse(url).hostname
    _, connect_cookies = load_substack_cookies()
    if hostname and hostname in connect_cookies:
        return

    if not click.confirm(
        "This is a Substack newsletter. Is it a paid subscription?", default=False
    ):
        return

    click.echo(
        "Make sure you're logged into substack.com in your browser."
        "\nNote: you may see a pop-up asking you to authenticate"
        " so we can read your login state."
    )
    browser = click.prompt("Which browser?", type=click.Choice(SUPPORTED_BROWSERS))
    _import_substack_cookie(browser)


@main.command()
def init():
    """Interactive first-run setup — Kindle email, Substack login, and feed configuration."""
    load_dotenv()
    sender = os.getenv("SENDER_EMAIL", "")
    if not sender:
        raise click.ClickException(
            "SENDER_EMAIL is not set. Add it to your .env file before running init."
        )

    # Step 1: Kindle email
    click.echo(
        "To find your Send to Kindle email address, go to:"
        "\nhttps://www.amazon.com/hz/mycd/digital-console/contentlist/pdocs/dateDsc"
        "\n  → Preferences → Personal Document Settings"
    )
    email = click.prompt("\nKindle email address")
    save_kindle_email_to_env(email)
    click.echo(f"Saved KINDLE_EMAIL={email} to .env")

    click.echo(
        f'\nWhile you\'re there, add "{sender}" to the Approved Personal Document E-mail List.'
    )
    click.confirm("Done?", default=True)

    # Step 2: Add feeds
    click.echo("\nAdd feed URLs (leave blank to finish):")
    while True:
        url = click.prompt("Feed URL", default="", show_default=False)
        if not url:
            break
        add_feed_to_env(url)
        click.echo(f"  Added: {url}")

    # Step 3: Substack login
    if click.confirm("\nDo you have any paid Substack subscriptions?"):
        click.echo(
            "Make sure you're logged into substack.com in your browser."
            "\nNote: you may see a pop-up asking you to authenticate"
            " so we can read your login state."
        )
        browser = click.prompt(
            "Which browser?",
            type=click.Choice(SUPPORTED_BROWSERS),
        )
        _import_substack_cookie(browser)

    click.echo("\nSetup complete! Run 'rss-to-kindle fetch' to send articles to your Kindle.")


@main.command()
@click.argument("url")
def remove(url):
    """Remove a feed URL from your configuration."""
    try:
        remove_feed_from_env(url)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"Removed: {url}")


@main.command()
@click.argument("url")
@click.option("--output", "-o", type=click.Path(), default=None, help="Output path for the EPUB.")
def preview(url, output):
    """Generate an EPUB locally without sending to Kindle."""
    session_cookie, connect_cookies = load_substack_cookies()

    click.echo(f"Fetching: {url}")
    # Fetch without cookies first to avoid sending auth to non-Substack sites
    raw_html = fetch_article_html(url)
    is_substack = _detect_substack(raw_html)

    if is_substack:
        has_cookie = bool(session_cookie or connect_cookies)
        # Re-fetch with cookies to unlock paywalled content
        if has_cookie:
            raw_html = fetch_article_html(
                url, session_cookie, is_substack=True, connect_cookies=connect_cookies
            )
        _check_substack_paywall(raw_html, has_cookie)

    article = extract_article(
        raw_html,
        author="Unknown",
        published="",
        session_cookie=session_cookie,
        is_substack=is_substack,
        url=url,
        connect_cookies=connect_cookies,
    )

    epub_data = build_epub(article)

    if output is None:
        safe_title = article.title.replace(":", " -")
        safe_title = "".join(c if c not in '/\\<>"|?*' else "_" for c in safe_title)
        output = f"{safe_title[:80]}.epub"

    Path(output).write_bytes(epub_data)
    click.echo(f"Saved: {output}")
