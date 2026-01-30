from dataclasses import dataclass

import feedparser


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
