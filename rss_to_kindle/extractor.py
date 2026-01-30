from dataclasses import dataclass

from readability import Document


@dataclass
class Article:
    title: str
    author: str
    published: str
    html_content: str


def extract_article(raw_html: str, author: str = "Unknown", published: str = "") -> Article:
    """Extract clean article content from raw HTML using readability."""
    doc = Document(raw_html)
    title = doc.title()
    content = doc.summary()

    return Article(
        title=title,
        author=author,
        published=published,
        html_content=content,
    )
