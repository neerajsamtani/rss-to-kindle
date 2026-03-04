import mimetypes
import smtplib
from email.message import EmailMessage
from io import BytesIO
from textwrap import wrap

from ebooklib import epub
from PIL import Image, ImageDraw, ImageFont

from .extractor import Article


def _download_cover_image(url: str) -> bytes | None:
    """Fetch the og:image, return raw bytes or None on failure."""
    import httpx

    try:
        resp = httpx.get(url, follow_redirects=True, timeout=15)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


def _generate_cover(title: str, author: str, background_image: bytes | None = None) -> bytes:
    """Generate a cover image (600x900) with title and author.

    When background_image is provided, it is resized to fill the cover and a dark
    gradient overlay is drawn on the bottom portion for text legibility. Text is
    anchored to the bottom. Otherwise falls back to a plain dark cover with centered text.
    """
    width, height = 600, 900

    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
        author_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 32)
    except OSError:
        title_font = ImageFont.load_default(48)
        author_font = ImageFont.load_default(32)

    title_lines = wrap(title, width=20)
    line_height = 62

    if background_image:
        try:
            img = Image.open(BytesIO(background_image)).convert("RGB")
        except Exception:
            background_image = None

    if background_image:
        # Resize to cover: scale so the image fills 600x900, then center-crop
        src_w, src_h = img.size
        scale = max(width / src_w, height / src_h)
        new_w, new_h = int(src_w * scale), int(src_h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - width) // 2
        top = (new_h - height) // 2
        img = img.crop((left, top, left + width, top + height))

        # Dark gradient overlay on the bottom ~40%
        gradient = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gradient_draw = ImageDraw.Draw(gradient)
        gradient_start = int(height * 0.55)
        for y_pos in range(gradient_start, height):
            alpha = int(210 * (y_pos - gradient_start) / (height - gradient_start))
            gradient_draw.line([(0, y_pos), (width, y_pos)], fill=(0, 0, 0, alpha))
        img = Image.alpha_composite(img.convert("RGBA"), gradient).convert("RGB")

        draw = ImageDraw.Draw(img)

        # Anchor text to bottom
        total_text_height = len(title_lines) * line_height + 20 + 40  # title + gap + author
        y = height - total_text_height - 40  # 40px bottom padding

        for line in title_lines:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            text_width = bbox[2] - bbox[0]
            draw.text(((width - text_width) / 2, y), line, fill="white", font=title_font)
            y += line_height

        y += 20
        bbox = draw.textbbox((0, 0), author, font=author_font)
        text_width = bbox[2] - bbox[0]
        draw.text(((width - text_width) / 2, y), author, fill=(200, 200, 200), font=author_font)
    else:
        img = Image.new("RGB", (width, height), color=(30, 30, 30))
        draw = ImageDraw.Draw(img)

        total_text_height = len(title_lines) * line_height + 60 + 40
        y = (height - total_text_height) // 2

        for line in title_lines:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            text_width = bbox[2] - bbox[0]
            draw.text(((width - text_width) / 2, y), line, fill="white", font=title_font)
            y += line_height

        y += 60
        bbox = draw.textbbox((0, 0), author, font=author_font)
        text_width = bbox[2] - bbox[0]
        draw.text(((width - text_width) / 2, y), author, fill=(180, 180, 180), font=author_font)

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def build_epub(article: Article) -> bytes:
    """Build an EPUB file from an article with embedded images and cover."""
    book = epub.EpubBook()
    book.set_identifier(f"rss-to-kindle-{hash(article.title)}")
    book.set_title(article.title)
    book.set_language("en")
    book.add_author(article.author)

    # Cover — use og:image as hero background when available
    background = None
    if article.cover_image_url:
        background = _download_cover_image(article.cover_image_url)
    cover_data = _generate_cover(article.title, article.author, background_image=background)
    book.set_cover("cover.jpg", cover_data)

    # Embed article images
    for filename, data in article.images.items():
        media_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
        img_item = epub.EpubItem(
            uid=filename,
            file_name=f"images/{filename}",
            media_type=media_type,
            content=data,
        )
        book.add_item(img_item)

    # Article chapter
    style = epub.EpubItem(
        uid="style",
        file_name="style.css",
        media_type="text/css",
        content=b"figcaption { text-align: center; }",
    )
    book.add_item(style)

    chapter = epub.EpubHtml(title=article.title, file_name="article.xhtml", lang="en")
    chapter.add_item(style)
    chapter.content = (
        f"<h1>{article.title}</h1>"
        f"<p><em>By {article.author} · {article.published}</em></p>"
        f"{article.html_content}"
    )
    book.add_item(chapter)

    book.toc = [chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]

    buf = BytesIO()
    epub.write_epub(buf, book)
    return buf.getvalue()


def send_to_kindle(
    article: Article,
    kindle_email: str,
    sender_email: str,
    sender_password: str,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
) -> None:
    """Send an article as an EPUB attachment to a Kindle email address."""
    msg = EmailMessage()
    msg["Subject"] = article.title
    msg["From"] = sender_email
    msg["To"] = kindle_email
    msg.set_content(f"Article: {article.title}")

    epub_data = build_epub(article)
    safe_title = article.title.replace(":", " -")
    safe_title = "".join(c if c not in '/\\<>"|?*' else "_" for c in safe_title)
    filename = f"{safe_title[:80]}.epub"

    msg.add_attachment(
        epub_data,
        maintype="application",
        subtype="epub+zip",
        filename=filename,
    )

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)


def send_expiry_notification(
    warnings: list[str],
    sender_email: str,
    sender_password: str,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
) -> None:
    """Send a notification email to the sender when cookies are expired or expiring soon."""
    msg = EmailMessage()
    msg["Subject"] = "Action required: rss-to-kindle authentication expiring"
    msg["From"] = sender_email
    msg["To"] = sender_email
    warning_lines = "\n".join(f"  - {w}" for w in warnings)
    body = (
        "Your rss-to-kindle service uses Substack session cookies to access paid newsletter "
        "content. One or more of these cookies has expired or is about to expire, which means "
        "paywalled articles will no longer be fetched and sent to your Kindle.\n\n"
        f"Affected cookies:\n{warning_lines}\n\n"
        "To fix this, run the following command on your local machine "
        "(you must be logged into Substack in the browser you specify):\n\n"
        "  rss-to-kindle substack-login --from-browser <browser>\n\n"
        "If you're running rss-to-kindle via GitHub Actions:\n"
        "  1. Run the command above locally to extract updated cookies from your browser.\n"
        "  2. Copy the new cookie values printed by the command.\n"
        "  3. Update the SUBSTACK_SESSION_COOKIE (and/or SUBSTACK_CONNECT_COOKIES) secrets\n"
        "     in your GitHub repository: Settings → Secrets and variables → Actions.\n"
        "  4. Trigger a manual run of the fetch workflow to confirm it works."
    )
    msg.set_content(body)
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
