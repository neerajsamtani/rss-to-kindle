import os
import time
from pathlib import Path

import click
from dotenv import load_dotenv

from .config import add_feed_to_env, load_config, load_feeds, save_cookie_to_env
from .extractor import extract_article
from .feed import fetch_feed, filter_recent
from .fetcher import fetch_article_html
from .kindle import build_epub, send_to_kindle
from .state import get_history, is_sent, mark_sent


def _process_feeds(config: dict, latest_only: bool = False, days: int = 3) -> int:
    """Check all feeds, fetch new articles, and send to Kindle. Returns count of articles sent."""
    sent_count = 0

    for feed_url in config["substack_feeds"]:
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
                raw_html = fetch_article_html(feed_article.url, config["substack_session_cookie"])
            except Exception as e:
                click.echo(f"    Error fetching article: {e}", err=True)
                continue

            article = extract_article(
                raw_html,
                author=feed_article.author,
                published=feed_article.published,
                session_cookie=config["substack_session_cookie"],
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
    """Monitor Substack RSS feeds and send articles to Kindle."""
    pass


SUPPORTED_BROWSERS = ["chrome", "firefox", "opera", "edge", "chromium"]


@main.command()
@click.option(
    "--from-browser",
    required=True,
    type=click.Choice(SUPPORTED_BROWSERS),
    help="Browser to read the Substack session cookie from.",
)
def login(from_browser):
    """Import your Substack session cookie from a browser."""
    import browser_cookie3

    loader = getattr(browser_cookie3, from_browser)
    try:
        jar = loader(domain_name=".substack.com")
    except Exception as e:
        raise click.ClickException(f"Could not read cookies from {from_browser}: {e}") from e

    cookie_value = None
    for cookie in jar:
        if cookie.name == "substack.sid" and "substack.com" in cookie.domain:
            cookie_value = cookie.value
            break

    if not cookie_value:
        raise click.ClickException(
            f"No Substack session cookie found in {from_browser}.\n"
            "Log into substack.com in that browser first, then re-run this command."
        )

    save_cookie_to_env(cookie_value)
    click.echo(f"Found Substack session cookie from {from_browser}.")
    click.echo("Saved to .env — you're all set.")


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
    """Show configured Substack feed URLs."""
    feeds = load_feeds()
    if not feeds:
        click.echo("No feeds configured. Add one with: rss-to-kindle add <url>")
        return

    for i, url in enumerate(feeds, 1):
        click.echo(f"  {i}. {url}")


@main.command()
@click.argument("url")
def add(url):
    """Add a Substack feed URL to your configuration."""
    feeds = load_feeds()
    if url in feeds:
        click.echo(f"Feed already configured: {url}")
        return

    add_feed_to_env(url)
    click.echo(f"Added: {url}")


@main.command()
@click.argument("url")
@click.option("--output", "-o", type=click.Path(), default=None, help="Output path for the EPUB.")
def preview(url, output):
    """Generate an EPUB locally without sending to Kindle."""
    load_dotenv()
    cookie = os.getenv("SUBSTACK_SESSION_COOKIE", "")
    if not cookie:
        raise click.ClickException("Missing SUBSTACK_SESSION_COOKIE in .env")

    click.echo(f"Fetching: {url}")
    raw_html = fetch_article_html(url, cookie)
    article = extract_article(raw_html, author="Unknown", published="", session_cookie=cookie)

    epub_data = build_epub(article)

    if output is None:
        safe_title = article.title.replace(":", " -")
        safe_title = "".join(c if c not in '/\\<>"|?*' else "_" for c in safe_title)
        output = f"{safe_title[:80]}.epub"

    Path(output).write_bytes(epub_data)
    click.echo(f"Saved: {output}")
