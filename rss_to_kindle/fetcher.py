import httpx


def fetch_article_html(url: str, session_cookie: str) -> str:
    """Fetch full article HTML using the Substack session cookie."""
    cookies = {"substack.sid": session_cookie}
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }

    response = httpx.get(url, cookies=cookies, headers=headers, follow_redirects=True, timeout=30)
    response.raise_for_status()
    return response.text
