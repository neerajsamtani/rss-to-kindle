import httpx


def fetch_article_html(url: str, session_cookie: str = "", is_substack: bool = False) -> str:
    """Fetch full article HTML. Uses the Substack session cookie when available."""
    cookies = {"substack.sid": session_cookie} if session_cookie and is_substack else {}
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    response = httpx.get(url, cookies=cookies, headers=headers, follow_redirects=True, timeout=30)
    response.raise_for_status()
    return response.text
