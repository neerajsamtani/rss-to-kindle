import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import lxml.etree
import lxml.html
from readability import Document
from readability.cleaners import html_cleaner

from .fetcher import (
    MAX_IMAGE_BYTES,
    HTTPStatusError,
    PublicFetchError,
    UnsafeURL,
    safe_http_get,
    substack_cookies_for_url,
)

MAX_ARTICLE_IMAGES = 30
MAX_TOTAL_IMAGE_BYTES = 48 * 1024 * 1024
MAX_IMAGE_DOWNLOAD_SECONDS = 45


class LoginRequiredError(RuntimeError):
    def __init__(self, had_cookie: bool):
        self.had_cookie = had_cookie
        if had_cookie:
            message = (
                "Substack sent this article to a login page. The saved login may be missing or "
                "expired; refresh it with `rss-to-kindle substack-login --from-browser <browser>` "
                "and retry."
            )
        else:
            message = (
                "This article requires Substack login. Sign in with `rss-to-kindle "
                "substack-login --from-browser <browser>`, then retry."
            )
        super().__init__(message)


class SubscriptionRequiredError(RuntimeError):
    def __init__(self, had_cookie: bool):
        self.had_cookie = had_cookie
        if had_cookie:
            message = (
                "Substack still shows a subscription wall. The saved login may have expired, "
                "or its account may not have access. Refresh the login with `rss-to-kindle "
                "substack-login --from-browser <browser>` and confirm that account has access."
            )
        else:
            message = (
                "This article is behind a Substack subscription. Sign in with `rss-to-kindle "
                "substack-login --from-browser <browser>` and confirm that account has access."
            )
        super().__init__(message)


class EmptyArticleError(RuntimeError):
    """Readability did not find any article text or images."""


@dataclass
class Article:
    title: str
    author: str
    published: str
    html_content: str
    images: dict[str, bytes] = field(default_factory=dict)
    cover_image_url: str | None = None


def _extract_meta(raw_html: str) -> dict[str, str | None]:
    """Extract article metadata before readability strips its containing head."""
    doc = lxml.html.fromstring(raw_html)
    result: dict[str, str | None] = {
        "og_image": None,
        "og_title": None,
        "author": None,
        "published": None,
    }

    for prop, key in [("og:image", "og_image"), ("og:title", "og_title")]:
        meta = doc.find(f'.//meta[@property="{prop}"]')
        if meta is not None:
            value = (meta.get("content") or "").strip()
            if value:
                result[key] = value

    author_meta = doc.find('.//meta[@name="author"]')
    if author_meta is not None:
        value = (author_meta.get("content") or "").strip()
        if value:
            result["author"] = value

    published_meta = doc.find('.//meta[@property="article:published_time"]')
    if published_meta is None:
        published_meta = doc.find('.//meta[@name="date"]')
    if published_meta is not None:
        value = (published_meta.get("content") or "").strip()
        if value:
            result["published"] = value

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
    base_url: str = "",
) -> tuple[str, dict[str, bytes]]:
    """Download images from HTML, rewrite src attributes, return modified HTML and image data."""
    doc = lxml.html.fromstring(html_content)
    images: dict[str, bytes] = {}
    deadline = time.monotonic() + MAX_IMAGE_DOWNLOAD_SECONDS
    downloaded_bytes = 0

    for image_index, img in enumerate(doc.iter("img")):
        for attr in ("srcset", "sizes"):
            img.attrib.pop(attr, None)
        src = img.get("src")
        if not src:
            continue
        if (
            image_index >= MAX_ARTICLE_IMAGES
            or time.monotonic() > deadline
            or downloaded_bytes >= MAX_TOTAL_IMAGE_BYTES
        ):
            img.attrib.pop("src", None)
            continue
        if not src.startswith(("http://", "https://")):
            if base_url:
                src = urljoin(base_url, src)
            else:
                img.attrib.pop("src", None)
                continue
        try:
            resp = safe_http_get(
                src,
                max_bytes=min(MAX_IMAGE_BYTES, MAX_TOTAL_IMAGE_BYTES - downloaded_bytes),
            )
            if resp.status_code >= 400:
                img.attrib.pop("src", None)
                continue
        except (PublicFetchError, UnsafeURL):
            img.attrib.pop("src", None)
            continue

        downloaded_bytes += len(resp.content)
        compressed = _compress_image(resp.content)
        if compressed != resp.content:
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

    for source in doc.iter("source"):
        for attr in ("src", "srcset", "sizes"):
            source.attrib.pop(attr, None)

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
    """Extract clean article content; cookie parameters remain for API compatibility.

    This function never uses them. Image assets are fetched as public requests without cookies.
    """
    meta = _extract_meta(raw_html)
    raw_html = _simplify_headers(raw_html)
    if is_substack:
        raw_html = _simplify_images(raw_html)

    title = (meta["og_title"] or Document(raw_html).title() or "Untitled").strip()
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

    content, images = _download_images(content, base_url=url)

    cover_image_url = meta["og_image"]
    if cover_image_url and url:
        cover_image_url = urljoin(url, cover_image_url)

    return Article(
        title=title,
        author=author,
        published=published or meta["published"] or "",
        html_content=content,
        images=images,
        cover_image_url=cover_image_url,
    )


def _looks_like_substack(raw_html: str, url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host == "substack.com" or host.endswith(".substack.com"):
        return True

    try:
        doc = lxml.html.fromstring(raw_html)
    except (ValueError, lxml.etree.ParserError):
        return False
    generator = doc.find('.//meta[@name="generator"]')
    if generator is not None and "substack" in (generator.get("content") or "").lower():
        return True
    if doc.find_class("captioned-image-container"):
        return True
    for element in doc.iter("script", "link"):
        resource_url = element.get("src") or element.get("href") or ""
        resource_host = (urlparse(resource_url).hostname or "").lower()
        if resource_host == "substackcdn.com" or resource_host.endswith(".substackcdn.com"):
            return True
    return False


def _class_tokens(doc: lxml.html.HtmlElement) -> set[str]:
    return {
        token.lower() for element in doc.iter() for token in (element.get("class") or "").split()
    }


def _is_paywall_page(raw_html: str) -> bool:
    """Use actual parsed lock markers, not incidental text or subscribe buttons."""
    try:
        doc = lxml.html.fromstring(raw_html)
    except (ValueError, lxml.etree.ParserError):
        return False

    markers = {
        "paywall",
        "paywall-content",
        "paywall-preview",
        "paywall-login",
        "paywall-overlay",
        "subscriber-only",
        "subscription-required",
        "paid-content-locked",
    }
    if _class_tokens(doc) & markers:
        return True
    locked_values = {"true", "locked", "subscriber-only", "paid"}
    return bool(
        doc.xpath(
            "//*[@data-paywall or @data-content-locked or @data-access-level="
            '"subscriber-only" or @data-access-level="paid"]'
        )
        and any(
            (element.get("data-paywall", "").lower() in locked_values)
            or (element.get("data-content-locked", "").lower() in locked_values)
            or (element.get("data-access-level", "").lower() in locked_values)
            for element in doc.xpath(
                "//*[@data-paywall or @data-content-locked or @data-access-level]"
            )
        )
    )


def _is_login_page(raw_html: str, final_url: str, is_substack: bool) -> bool:
    path = (urlparse(final_url).path or "").lower().rstrip("/")
    login_paths = {"/login", "/signin", "/sign-in", "/log-in", "/account/login"}
    if is_substack and (path in login_paths or path.endswith("/sign-in")):
        return True

    try:
        doc = lxml.html.fromstring(raw_html)
    except (ValueError, lxml.etree.ParserError):
        return False
    if _class_tokens(doc) & {"login-page", "signin-page", "sign-in-page", "auth-login"}:
        return True

    heading = " ".join(doc.xpath("//title/text() | //h1/text() | //h2/text()")).lower()
    has_password = bool(doc.xpath('//input[@type="password"]'))
    return bool(
        has_password
        and any(phrase in heading for phrase in ("sign in", "log in", "login", "welcome back"))
    )


def _article_has_content(article: Article) -> bool:
    try:
        doc = lxml.html.fromstring(article.html_content)
    except (ValueError, lxml.etree.ParserError):
        return False
    return bool(doc.text_content().strip() or doc.xpath(".//img[@src]"))


def extract_url_article(
    url: str,
    config: dict,
    on_stage: Callable[[str], None] | None = None,
) -> Article:
    """Fetch and extract a one-off URL without requiring a configured RSS feed."""

    def stage_callback(stage: str) -> None:
        if on_stage is not None:
            on_stage(stage)

    session_cookie = config.get("substack_session_cookie", "")
    connect_cookies = config.get("substack_connect_cookies", {})

    stage_callback("fetching")
    scoped_cookies = substack_cookies_for_url(url, session_cookie, connect_cookies)
    page = safe_http_get(url, cookies=scoped_cookies)
    auth_cookie_sent = bool(scoped_cookies)

    # A redirect hop never carries cookies. If a public vanity URL redirected to a
    # canonical HTTPS Substack page, retry that final origin with only its own scoped
    # cookie; this keeps redirect handling itself credential-free.
    looks_like_substack = _looks_like_substack(page.text, page.url)
    redirected_to_https = page.url != url and urlparse(page.url).scheme.lower() == "https"
    if redirected_to_https:
        final_cookies = substack_cookies_for_url(page.url, session_cookie, connect_cookies)
        if final_cookies:
            page = safe_http_get(page.url, cookies=final_cookies)
            auth_cookie_sent = True
            looks_like_substack = _looks_like_substack(page.text, page.url)

    raw_html = page.text
    has_cookie = auth_cookie_sent
    if page.status_code == 401 and looks_like_substack:
        raise LoginRequiredError(has_cookie)
    if page.status_code == 403 and _is_paywall_page(raw_html):
        raise SubscriptionRequiredError(has_cookie)
    if page.status_code >= 400:
        raise HTTPStatusError(page.status_code)

    if _is_login_page(raw_html, page.url, looks_like_substack or has_cookie):
        raise LoginRequiredError(has_cookie)
    if _is_paywall_page(raw_html):
        raise SubscriptionRequiredError(has_cookie)

    stage_callback("extracting")
    try:
        article = extract_article(
            raw_html,
            author="Unknown",
            published="",
            is_substack=looks_like_substack,
            url=page.url,
        )
    except (PublicFetchError, UnsafeURL):
        raise
    except Exception as exc:
        raise EmptyArticleError("The page could not be extracted as an article.") from exc

    if not _article_has_content(article):
        raise EmptyArticleError("No readable article content was found on this page.")
    return article
