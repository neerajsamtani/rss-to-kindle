import hashlib
import json
from dataclasses import dataclass, field

import httpx
import lxml.html
from readability import Document


@dataclass
class Article:
    title: str
    author: str
    published: str
    html_content: str
    images: dict[str, bytes] = field(default_factory=dict)
    cover_image_url: str | None = None


def _extract_meta(raw_html: str) -> dict[str, str | None]:
    """Extract og:image, og:title, and author from meta tags before readability strips them."""
    doc = lxml.html.fromstring(raw_html)
    result: dict[str, str | None] = {"og_image": None, "og_title": None, "author": None}

    for prop, key in [("og:image", "og_image"), ("og:title", "og_title")]:
        meta = doc.find(f'.//meta[@property="{prop}"]')
        if meta is not None:
            value = meta.get("content", "").strip()
            if value:
                result[key] = value

    author_meta = doc.find('.//meta[@name="author"]')
    if author_meta is not None:
        value = author_meta.get("content", "").strip()
        if value:
            result["author"] = value

    return result


def _simplify_headers(raw_html: str) -> str:
    """Strip Substack's anchor widgets from headings so readability preserves them.

    Substack injects a div.header-anchor-parent with nested button/svg inside each
    heading for the anchor-link icon. The class "header-anchor-post" on the heading
    itself triggers readability's unlikely-candidate filter (matches "header").
    We remove the widget div and strip heading classes to prevent both issues.
    """
    doc = lxml.html.fromstring(raw_html)

    for tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        for heading in doc.iter(tag):
            for widget in heading.find_class("header-anchor-parent"):
                heading.remove(widget)
            if "class" in heading.attrib:
                del heading.attrib["class"]

    return lxml.html.tostring(doc, encoding="unicode")


def _simplify_images(raw_html: str) -> str:
    """Replace Substack's complex image markup with plain <img> tags.

    Readability strips images buried in deep div nesting (captioned-image-container
    > figure > a > div > picture > img). This hoists each image out, replacing
    the outermost wrapper with a simple <img> so readability preserves it.
    """
    doc = lxml.html.fromstring(raw_html)

    for container in doc.find_class("captioned-image-container"):
        img = container.find(".//img[@data-attrs]")
        if img is None:
            continue
        try:
            src = json.loads(img.get("data-attrs")).get("src", "")
        except (json.JSONDecodeError, TypeError):
            continue
        if not src:
            continue

        new_figure = lxml.html.Element("figure")
        new_img = lxml.html.Element("img")
        new_img.set("src", src)
        new_figure.append(new_img)
        figcaption = container.find(".//figcaption")
        if figcaption is not None:
            new_figure.append(figcaption)
        container.getparent().replace(container, new_figure)

    return lxml.html.tostring(doc, encoding="unicode")


def _download_images(html_content: str, session_cookie: str) -> tuple[str, dict[str, bytes]]:
    """Download images from HTML, rewrite src attributes, return modified HTML and image data."""
    doc = lxml.html.fromstring(html_content)
    images: dict[str, bytes] = {}
    cookies = {"substack.sid": session_cookie}
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    for img in doc.iter("img"):
        src = img.get("src")
        if not src or not src.startswith(("http://", "https://")):
            continue
        try:
            resp = httpx.get(
                src, cookies=cookies, headers=headers, follow_redirects=True, timeout=15
            )
            resp.raise_for_status()
        except Exception:
            continue

        ext = "jpg"
        content_type = resp.headers.get("content-type", "")
        if "png" in content_type:
            ext = "png"
        elif "gif" in content_type:
            ext = "gif"
        elif "webp" in content_type:
            ext = "webp"

        filename = f"{hashlib.md5(src.encode()).hexdigest()}.{ext}"
        images[filename] = resp.content
        img.set("src", f"images/{filename}")

    modified_html = lxml.html.tostring(doc, encoding="unicode")
    return modified_html, images


def extract_article(
    raw_html: str, author: str = "Unknown", published: str = "", session_cookie: str = ""
) -> Article:
    """Extract clean article content from raw HTML using readability."""
    meta = _extract_meta(raw_html)
    raw_html = _simplify_headers(raw_html)
    raw_html = _simplify_images(raw_html)

    doc = Document(raw_html)
    title = meta["og_title"] or doc.title()
    if author == "Unknown" and meta["author"]:
        author = meta["author"]
    content = doc.summary()

    images: dict[str, bytes] = {}
    if session_cookie:
        content, images = _download_images(content, session_cookie)

    return Article(
        title=title,
        author=author,
        published=published,
        html_content=content,
        images=images,
        cover_image_url=meta["og_image"],
    )
