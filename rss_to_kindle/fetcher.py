import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx
from httpcore._backends.sync import SyncBackend

MAX_REDIRECTS = 8
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
REQUEST_DEADLINE_SECONDS = 25
USER_AGENT = "Mozilla/5.0 (compatible; rss-to-kindle/1.0)"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class UnsafeURL(ValueError):
    """The URL does not point to a public HTTP(S) endpoint."""


class PublicFetchError(RuntimeError):
    """A safe public HTTP request failed or exceeded a configured bound."""


class HTTPStatusError(PublicFetchError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"The page returned HTTP {status_code}.")


@dataclass(frozen=True)
class FetchedPage:
    status_code: int
    headers: httpx.Headers
    content: bytes
    url: str

    @property
    def text(self) -> str:
        request = httpx.Request("GET", self.url)
        decoded_headers = self.headers.copy()
        decoded_headers.pop("content-encoding", None)
        decoded_headers.pop("content-length", None)
        return httpx.Response(
            self.status_code, headers=decoded_headers, content=self.content, request=request
        ).text


def _validated_url(url: str) -> tuple[str, str, int, list[str]]:
    """Validate URL syntax and pin every DNS answer before making a request."""
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port if parsed.port is not None else (443 if scheme == "https" else 80)
    except (TypeError, ValueError) as exc:
        raise UnsafeURL("Only public HTTP and HTTPS URLs are allowed.") from exc

    if (
        scheme not in {"http", "https"}
        or not parsed.netloc
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {80, 443}
    ):
        raise UnsafeURL("Only public HTTP and HTTPS URLs on ports 80 or 443 are allowed.")

    host = host.rstrip(".").lower()
    if not host or any(character.isspace() for character in host):
        raise UnsafeURL("Only public HTTP and HTTPS URLs are allowed.")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeURL("Only public HTTP and HTTPS URLs are allowed.") from exc

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise PublicFetchError("The public host could not be resolved.") from exc
        addresses = list(dict.fromkeys(record[4][0].split("%", 1)[0] for record in records))
    else:
        addresses = [str(literal)]

    if not addresses:
        raise PublicFetchError("The public host could not be resolved.")
    try:
        parsed_addresses = [ipaddress.ip_address(address) for address in addresses]
    except ValueError as exc:
        raise PublicFetchError("The public host returned an invalid address.") from exc
    if any(
        not address.is_global or address.is_multicast or address.is_unspecified
        for address in parsed_addresses
    ):
        raise UnsafeURL("The URL resolves to a non-public network address.")

    return scheme, host, port, [str(address) for address in parsed_addresses]


class _PinnedBackend(SyncBackend):
    """Connect only to the public IPs validated for this request."""

    def __init__(self, host: str, addresses: list[str]):
        self.host = host
        self.addresses = addresses

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: list[tuple[int, int, int]] | None = None,
    ) -> httpcore.NetworkStream:
        if host.rstrip(".").lower() != self.host:
            raise OSError("The request attempted to connect to an unvalidated host.")

        last_error = None
        for address in self.addresses:
            try:
                # The HTTP origin remains the hostname, so httpcore still sends the
                # correct Host header and uses it for TLS certificate validation/SNI.
                return super().connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError("The public host has no validated addresses.")


def _request_once(
    url: str,
    host: str,
    addresses: list[str],
    cookies: dict[str, str],
    headers: dict[str, str],
    max_bytes: int,
) -> FetchedPage:
    transport = httpx.HTTPTransport(trust_env=False, retries=0)
    transport._pool._network_backend = _PinnedBackend(host, addresses)
    timeout = httpx.Timeout(20, connect=5, read=6, write=5, pool=5)
    try:
        with httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream("GET", url, headers=headers, cookies=cookies) as response:
                response_headers = httpx.Headers(response.headers)
                page_url = str(response.url)
                if response.status_code in REDIRECT_STATUSES:
                    return FetchedPage(response.status_code, response_headers, b"", page_url)

                length = response_headers.get("content-length")
                if length and length.isdigit() and int(length) > max_bytes:
                    raise PublicFetchError("The response exceeds the allowed download size.")

                body = bytearray()
                deadline = time.monotonic() + REQUEST_DEADLINE_SECONDS
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise PublicFetchError("The response took too long to download.")
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise PublicFetchError("The response exceeds the allowed download size.")
                return FetchedPage(
                    response.status_code,
                    response_headers,
                    bytes(body),
                    page_url,
                )
    except PublicFetchError:
        raise
    except UnsafeURL:
        raise
    except httpx.HTTPError as exc:
        raise PublicFetchError("The public page could not be fetched.") from exc


def safe_http_get(
    url: str,
    *,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> FetchedPage:
    """Fetch a public HTTP(S) URL, validating and pinning every redirect target.

    Credentials are sent only to the requested origin. Redirect requests use a new
    client without cookies so neither Cookie nor Set-Cookie state crosses a redirect.
    """
    current_url = url
    initial_cookies = dict(cookies or {})
    safe_headers = {"User-Agent": USER_AGENT}
    if headers:
        safe_headers.update(
            {
                name: value
                for name, value in headers.items()
                if name.lower() not in {"cookie", "authorization", "proxy-authorization", "host"}
            }
        )

    for redirect_count in range(MAX_REDIRECTS + 1):
        _, host, _, addresses = _validated_url(current_url)
        response = _request_once(
            current_url,
            host,
            addresses,
            initial_cookies if redirect_count == 0 else {},
            safe_headers,
            max_bytes,
        )
        location = response.headers.get("location")
        if response.status_code not in REDIRECT_STATUSES or not location:
            return response
        if redirect_count == MAX_REDIRECTS:
            raise PublicFetchError("The page redirected too many times.")
        next_url = urljoin(current_url, location)
        # Validate the complete redirect before attempting any connection. _request_once
        # will resolve and pin it again, so a DNS change cannot replace these addresses.
        _validated_url(next_url)
        current_url = next_url

    raise PublicFetchError("The page redirected too many times.")


def substack_cookies_for_url(
    url: str,
    session_cookie: str,
    connect_cookies: dict[str, str] | None,
) -> dict[str, str]:
    try:
        if urlsplit(url).scheme.lower() != "https":
            return {}
        host = (urlsplit(url).hostname or "").rstrip(".").lower().encode("idna").decode("ascii")
    except UnicodeError:
        return {}

    cookies: dict[str, str] = {}
    is_substack_host = host == "substack.com" or host.endswith(".substack.com")
    if is_substack_host:
        if session_cookie:
            cookies["substack.sid"] = session_cookie
        return cookies

    for domain, cookie in (connect_cookies or {}).items():
        try:
            configured_host = domain.rstrip(".").lower().encode("idna").decode("ascii")
        except (AttributeError, UnicodeError):
            continue
        if configured_host == host and cookie:
            cookies["connect.sid"] = cookie
            break
    return cookies


def fetch_article_page(
    url: str,
    session_cookie: str = "",
    is_substack: bool = False,
    connect_cookies: dict[str, str] | None = None,
) -> FetchedPage:
    """Fetch an article page, applying only host-scoped Substack cookies."""
    cookies = substack_cookies_for_url(url, session_cookie, connect_cookies) if is_substack else {}
    page = safe_http_get(url, cookies=cookies)
    if is_substack and page.url != url and urlsplit(page.url).scheme.lower() == "https":
        final_cookies = substack_cookies_for_url(page.url, session_cookie, connect_cookies)
        if final_cookies:
            page = safe_http_get(page.url, cookies=final_cookies)
    if page.status_code >= 400:
        raise HTTPStatusError(page.status_code)
    return page


def fetch_article_html(
    url: str,
    session_cookie: str = "",
    is_substack: bool = False,
    connect_cookies: dict[str, str] | None = None,
) -> str:
    """Fetch an article's HTML while keeping the original RSS API available."""
    return fetch_article_page(url, session_cookie, is_substack, connect_cookies).text
