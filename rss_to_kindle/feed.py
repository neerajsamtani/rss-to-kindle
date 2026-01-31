from calendar import timegm
from dataclasses import dataclass
from datetime import UTC, datetime

import feedparser
from feedparser.datetimes import _parse_date


@dataclass
class FeedArticle:
    title: str
    url: str
    published: str
    author: str


def fetch_feed(feed_url: str) -> list[FeedArticle]:
    """Parse an RSS feed and return a list of articles."""
    feed = feedparser.parse(feed_url)

    if feed.bozo and not feed.entries:
        raise RuntimeError(f"Failed to parse feed: {feed_url} — {feed.bozo_exception}")

    articles = []
    for entry in feed.entries:
        published = ""
        if hasattr(entry, "published"):
            published = entry.published
        elif hasattr(entry, "updated"):
            published = entry.updated

        articles.append(
            FeedArticle(
                title=entry.get("title", "Untitled"),
                url=entry.get("link", ""),
                published=published,
                author=entry.get("author", "Unknown"),
            )
        )

    return articles


def filter_recent(articles: list[FeedArticle], days: int) -> list[FeedArticle]:
    """Return only articles published within the last `days` days.

    Articles with unparsable dates are included as a safe default.
    """
    cutoff = datetime.now(UTC).timestamp() - (days * 86400)
    result = []
    for article in articles:
        parsed = _parse_date(article.published) if article.published else None
        if parsed is None or timegm(parsed) >= cutoff:
            result.append(article)
    return result
