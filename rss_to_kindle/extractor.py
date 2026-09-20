import hashlib
import json
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
import lxml.html
from readability import Document
from readability.cleaners import html_cleaner


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
    """Remove heading permalink widgets before readability strips their sizing."""
    doc = lxml.html.fromstring(raw_html)

    for tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        for heading in doc.iter(tag):
            for widget in heading.find_class("header-anchor-parent"):
                widget.drop_tree()
            for anchor in heading.iter("a"):
                classes = anchor.get("class", "").split()
                if "heading-anchor" in classes or (
                    anchor.get("href", "").startswith("#")
                    and not anchor.text_content().strip()
                    and anchor.xpath(".//svg | .//img")
                ):
                    anchor.drop_tree()
            if "class" in heading.attrib:
                del heading.attrib["class"]

    return lxml.html.tostring(doc, encoding="unicode")


def _extract_content(raw_html: str) -> str:
    """Let readability select the article without destroying lists or footnotes."""
    doc = html_cleaner.clean_html(lxml.html.fromstring(raw_html))
    preserved = {}
    selected = set()
    for element in list(doc.iter("*")):
        classes = element.get("class", "").split()
        is_note = any(c.startswith("footnote") for c in classes) or element.get("role") in (
            "doc-endnotes",
            "doc-endnote",
            "doc-noteref",
            "doc-backlink",
        )
        if element.tag not in ("ul", "ol") and not is_note:
            continue
        if element.getparent() is None or any(a in selected for a in element.iterancestors()):
            continue
        selected.add(element)
        # Text-only placeholders survive link-density and "foot" class filters.
        # Restore only those within the article that readability actually selected.
        placeholder = lxml.html.Element("span" if element.tag in ("a", "sup") else "p")
        key = str(len(preserved))
        placeholder.set("data-kindle-preserved", key)
        placeholder.text = element.text_content()
        placeholder.tail = element.tail
        element.getparent().replace(element, placeholder)
        preserved[key] = element

    content = Document(lxml.html.tostring(doc, encoding="unicode")).summary()
    doc = lxml.html.fromstring(content)
    for placeholder in doc.xpath("//*[@data-kindle-preserved]"):
        element = preserved[placeholder.get("data-kindle-preserved")]
        element.tail = placeholder.tail
        placeholder.getparent().replace(placeholder, element)
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


def _compress_image(data: bytes, max_width: int = 1200, quality: int = 80) -> bytes:
    """Resize and compress an image for Kindle. Returns original bytes if no resize needed."""
    from io import BytesIO

    from PIL import Image

    try:
        img = Image.open(BytesIO(data))
    except Exception:
        return data

    if getattr(img, "is_animated", False):
        return data

    if img.width <= max_width:
        return data

    ratio = max_width / img.width
    img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
    img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _download_images(
    html_content: str,
    session_cookie: str = "",
    base_url: str = "",
    connect_cookies: dict[str, str] | None = None,
) -> tuple[str, dict[str, bytes]]:
    """Download images from HTML, rewrite src attributes, return modified HTML and image data."""
    doc = lxml.html.fromstring(html_content)
    images: dict[str, bytes] = {}
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    for img in doc.iter("img"):
        src = img.get("src")
        if not src:
            continue
        if not src.startswith(("http://", "https://")):
            if base_url:
                src = urljoin(base_url, src)
            else:
                continue
        # Only send Substack cookies to Substack's CDN
        cookies = {}
        if "substackcdn.com" in src:
            if session_cookie:
                cookies["substack.sid"] = session_cookie
            # Use the connect.sid for the article's domain (not the CDN domain)
            connect_cookie = (connect_cookies or {}).get(urlparse(base_url).hostname, "")
            if connect_cookie:
                cookies["connect.sid"] = connect_cookie
        try:
            resp = httpx.get(
                src, cookies=cookies, headers=headers, follow_redirects=True, timeout=15
            )
            resp.raise_for_status()
        except Exception:
            continue

        compressed = _compress_image(resp.content)
        if compressed is not resp.content:
            filename = f"{hashlib.md5(src.encode()).hexdigest()}.jpg"
        else:
            ext = "jpg"
            content_type = resp.headers.get("content-type", "")
            if "png" in content_type:
                ext = "png"
            elif "gif" in content_type:
                ext = "gif"
            elif "webp" in content_type:
                ext = "webp"
            filename = f"{hashlib.md5(src.encode()).hexdigest()}.{ext}"
        images[filename] = compressed
        img.set("src", f"images/{filename}")
        # Strip responsive attributes that are meaningless in EPUB and confuse some readers
        for attr in ("srcset", "sizes"):
            if attr in img.attrib:
                del img.attrib[attr]

    modified_html = lxml.html.tostring(doc, encoding="unicode")
    return modified_html, images


def extract_article(
    raw_html: str,
    author: str = "Unknown",
    published: str = "",
    session_cookie: str = "",
    is_substack: bool = False,
    url: str = "",
    connect_cookies: dict[str, str] | None = None,
) -> Article:
    """Extract clean article content from raw HTML using readability."""
    meta = _extract_meta(raw_html)
    raw_html = _simplify_headers(raw_html)
    if is_substack:
        raw_html = _simplify_images(raw_html)

    title = meta["og_title"] or Document(raw_html).title()
    if author == "Unknown" and meta["author"]:
        author = meta["author"]
    content = _extract_content(raw_html)
    if url:
        doc = lxml.html.fromstring(content)
        for anchor in doc.iter("a"):
            href = anchor.get("href", "")
            if href and not href.startswith("#"):
                anchor.set("href", urljoin(url, href))
        content = lxml.html.tostring(doc, encoding="unicode")

    content, images = _download_images(
        content, session_cookie, base_url=url, connect_cookies=connect_cookies
    )

    return Article(
        title=title,
        author=author,
        published=published,
        html_content=content,
        images=images,
        cover_image_url=meta["og_image"],
    )
