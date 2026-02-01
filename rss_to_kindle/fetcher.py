from urllib.parse import urlparse

import httpx


def fetch_article_html(
    url: str,
    session_cookie: str = "",
    is_substack: bool = False,
    connect_cookies: dict[str, str] | None = None,
) -> str:
    """Fetch full article HTML. Uses the Substack session cookies when available."""
    cookies = {}
    if is_substack:
        if session_cookie:
            cookies["substack.sid"] = session_cookie
        connect_cookie = (connect_cookies or {}).get(urlparse(url).hostname, "")
        if connect_cookie:
            cookies["connect.sid"] = connect_cookie
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    response = httpx.get(url, cookies=cookies, headers=headers, follow_redirects=True, timeout=30)
    response.raise_for_status()
    return response.text
