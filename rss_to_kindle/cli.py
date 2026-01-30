import time

import click

from .config import load_config
from .extractor import extract_article
from .feed import fetch_feed
from .fetcher import fetch_article_html
from .kindle import send_to_kindle
from .state import is_sent, mark_sent


def _process_feeds(config: dict, latest_only: bool = False) -> int:
    """Check all feeds, fetch new articles, and send to Kindle. Returns count of articles sent."""
    sent_count = 0

    for feed_url in config["substack_feeds"]:
        click.echo(f"Checking feed: {feed_url}")
        try:
            articles = fetch_feed(feed_url)
        except Exception as e:
            click.echo(f"  Error parsing feed: {e}", err=True)
            continue

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

            article = extract_article(raw_html, author=feed_article.author, published=feed_article.published)

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

            mark_sent(feed_article.url)
            sent_count += 1
            click.echo(f"    Sent: {feed_article.title}")

    return sent_count


@click.group()
def main():
    """Monitor Substack RSS feeds and send articles to Kindle."""
    pass


@main.command()
@click.option("--latest", is_flag=True, help="Only fetch the latest article from each feed.")
def fetch(latest):
    """One-shot: check feeds, fetch & send any new articles."""
    config = load_config()
    sent = _process_feeds(config, latest_only=latest)
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
