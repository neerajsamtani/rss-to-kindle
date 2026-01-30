import smtplib
from email.message import EmailMessage

from .extractor import Article


def _build_kindle_html(article: Article) -> str:
    """Wrap article content in a clean HTML document suitable for Kindle."""
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{article.title}</title>
    <style>
        body {{ font-family: Georgia, serif; line-height: 1.6; margin: 1em; }}
        h1 {{ font-size: 1.5em; }}
        .meta {{ color: #666; font-size: 0.9em; margin-bottom: 1em; }}
        img {{ max-width: 100%; height: auto; }}
    </style>
</head>
<body>
    <h1>{article.title}</h1>
    <div class="meta">By {article.author} · {article.published}</div>
    {article.html_content}
</body>
</html>"""


def send_to_kindle(
    article: Article,
    kindle_email: str,
    sender_email: str,
    sender_password: str,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
) -> None:
    """Send an article as an HTML attachment to a Kindle email address."""
    msg = EmailMessage()
    msg["Subject"] = article.title
    msg["From"] = sender_email
    msg["To"] = kindle_email
    msg.set_content(f"Article: {article.title}")

    html_content = _build_kindle_html(article)
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in article.title)
    filename = f"{safe_title[:80]}.html"

    msg.add_attachment(
        html_content.encode("utf-8"),
        maintype="text",
        subtype="html",
        filename=filename,
    )

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
