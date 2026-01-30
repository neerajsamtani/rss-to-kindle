import mimetypes
import smtplib
from email.message import EmailMessage
from io import BytesIO
from textwrap import wrap

from ebooklib import epub
from PIL import Image, ImageDraw, ImageFont

from .extractor import Article


def _generate_cover(title: str, author: str) -> bytes:
    """Generate a simple cover image (600x800) with title and author."""
    width, height = 600, 900
    img = Image.new("RGB", (width, height), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)

    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
        author_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 32)
    except OSError:
        title_font = ImageFont.load_default(48)
        author_font = ImageFont.load_default(32)

    title_lines = wrap(title, width=20)

    # Vertically center the title block
    line_height = 62
    total_text_height = len(title_lines) * line_height + 60 + 40  # title + gap + author
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


def _build_epub(article: Article) -> bytes:
    """Build an EPUB file from an article with embedded images and cover."""
    book = epub.EpubBook()
    book.set_identifier(f"rss-to-kindle-{hash(article.title)}")
    book.set_title(article.title)
    book.set_language("en")
    book.add_author(article.author)

    # Cover
    cover_data = _generate_cover(article.title, article.author)
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

    epub_data = _build_epub(article)
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
