import os
import unittest
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

import httpcore
import httpx
from httpcore._sync.connection import HTTPConnection
from lxml import etree, html

from rss_to_kindle import config as config_module
from rss_to_kindle.extractor import (
    Article,
    SubscriptionRequiredError,
    _download_images,
    _is_paywall_page,
    extract_article,
    extract_url_article,
)
from rss_to_kindle.fetcher import (
    FetchedPage,
    UnsafeURL,
    _PinnedBackend,
    _validated_url,
    fetch_article_page,
    safe_http_get,
    substack_cookies_for_url,
)
from rss_to_kindle.kindle import build_epub
from rss_to_kindle.pipeline import (
    AccessError,
    DeliveryError,
    send_url_to_kindle,
)

ARTICLE_URL = "https://writer.example.com/posts/a-useful-article"
PROSE = (
    "This article explains how to build a useful habit with small, repeated steps. "
    "Readers can use the method to make steady progress on a difficult project. "
)
PUBLIC_ARTICLE_HTML = f"""<html><head><title>Useful Article</title>
<meta property="og:title" content="Useful Article"></head>
<body><nav>Menu</nav><article><p>{PROSE * 4}</p>
<p>{PROSE * 4}</p></article></body></html>"""


def fetched_page(
    body: str,
    *,
    status_code: int = 200,
    url: str = ARTICLE_URL,
    headers: dict[str, str] | None = None,
) -> FetchedPage:
    return FetchedPage(
        status_code,
        httpx.Headers(headers or {"content-type": "text/html; charset=utf-8"}),
        body.encode(),
        url,
    )


class ConfigTests(unittest.TestCase):
    def test_one_off_config_does_not_require_feeds(self) -> None:
        environment = {
            "KINDLE_EMAIL": "reader@kindle.com",
            "SENDER_EMAIL": "sender@example.com",
            "SENDER_PASSWORD": "test-secret",
        }
        with (
            patch("rss_to_kindle.config.load_dotenv"),
            patch.dict(os.environ, environment, clear=True),
        ):
            loaded = config_module.load_config(require_feeds=False)
        self.assertEqual(loaded["feeds"], [])
        self.assertEqual(loaded["kindle_email"], "reader@kindle.com")

    def test_default_config_still_requires_feeds(self) -> None:
        environment = {
            "KINDLE_EMAIL": "reader@kindle.com",
            "SENDER_EMAIL": "sender@example.com",
            "SENDER_PASSWORD": "test-secret",
        }
        with (
            patch("rss_to_kindle.config.load_dotenv"),
            patch.dict(os.environ, environment, clear=True),
        ):
            with self.assertRaisesRegex(ValueError, "FEEDS"):
                config_module.load_config()


class PublicURLTests(unittest.TestCase):
    def test_fetched_text_does_not_decode_http_content_encoding_twice(self) -> None:
        page = fetched_page(
            "decoded text",
            headers={
                "content-type": "text/plain; charset=utf-8",
                "content-encoding": "gzip",
                "content-length": "100",
            },
        )
        self.assertEqual(page.text, "decoded text")

    def test_private_or_unsupported_destinations_are_rejected(self) -> None:
        for url in (
            "http://127.0.0.1/article",
            "http://192.168.1.2/article",
            "http://224.0.0.1/article",
            "file:///etc/passwd",
            "https://user:password@example.com/article",
            "https://example.com:8080/article",
        ):
            with self.subTest(url=url), self.assertRaises(UnsafeURL):
                _validated_url(url)

    def test_dns_answers_must_all_be_public(self) -> None:
        private_record = (
            2,
            1,
            6,
            "",
            ("10.0.0.4", 443),
        )
        with patch("rss_to_kindle.fetcher.socket.getaddrinfo", return_value=[private_record]):
            with self.assertRaises(UnsafeURL):
                _validated_url("https://private-looking.example/article")

    def test_socket_connect_uses_the_validated_ip(self) -> None:
        backend = _PinnedBackend("news.example", ["93.184.216.34"])
        stream = object()
        with patch.object(httpcore.SyncBackend, "connect_tcp", return_value=stream) as connect:
            self.assertIs(backend.connect_tcp("news.example", 443), stream)
        self.assertEqual(connect.call_args.args[0], "93.184.216.34")

    def test_pinned_connection_keeps_the_original_tls_hostname(self) -> None:
        class FakeStream:
            server_hostname = None

            def start_tls(self, *, ssl_context, server_hostname, timeout):
                self.server_hostname = server_hostname
                return self

        stream = FakeStream()
        backend = _PinnedBackend("news.example", ["93.184.216.34"])
        origin = httpcore.Origin(scheme=b"https", host=b"news.example", port=443)
        connection = HTTPConnection(origin=origin, network_backend=backend)
        request = httpcore.Request("GET", "https://news.example/article")
        with patch.object(httpcore.SyncBackend, "connect_tcp", return_value=stream) as connect:
            connection._connect(request)
        self.assertEqual(connect.call_args.args[0], "93.184.216.34")
        self.assertEqual(stream.server_hostname, "news.example")

    def test_substack_cookies_are_scoped_and_require_https(self) -> None:
        connect = {"newsletter.example.com": "custom-secret"}
        self.assertEqual(
            substack_cookies_for_url("https://writer.substack.com/post", "session-secret", connect),
            {"substack.sid": "session-secret"},
        )
        self.assertEqual(
            substack_cookies_for_url(
                "https://newsletter.example.com/post", "session-secret", connect
            ),
            {"connect.sid": "custom-secret"},
        )
        self.assertEqual(
            substack_cookies_for_url(
                "https://sub.newsletter.example.com/post", "session-secret", connect
            ),
            {},
        )
        self.assertEqual(
            substack_cookies_for_url("http://writer.substack.com/post", "session-secret", connect),
            {},
        )

    def test_redirected_substack_fetch_retries_only_on_canonical_https_host(self) -> None:
        redirected = fetched_page(
            "<html><body>Login</body></html>",
            url="https://writer.substack.com/p/article",
        )
        unlocked = fetched_page(
            PUBLIC_ARTICLE_HTML,
            url="https://writer.substack.com/p/article",
        )
        with patch(
            "rss_to_kindle.fetcher.safe_http_get", side_effect=[redirected, unlocked]
        ) as get:
            page = fetch_article_page(
                "http://writer.substack.com/p/article",
                session_cookie="session-secret",
                is_substack=True,
            )
        self.assertEqual(page.text, PUBLIC_ARTICLE_HTML)
        self.assertEqual(get.call_args_list[0].kwargs["cookies"], {})
        self.assertEqual(
            get.call_args_list[1].kwargs["cookies"], {"substack.sid": "session-secret"}
        )

    def test_redirects_do_not_forward_cookies(self) -> None:
        first = FetchedPage(
            302,
            httpx.Headers({"location": "https://canonical.example/article"}),
            b"",
            "https://original.example/article",
        )
        second = fetched_page("ok", url="https://canonical.example/article")
        with (
            patch(
                "rss_to_kindle.fetcher._validated_url",
                side_effect=[
                    ("https", "original.example", 443, ["93.184.216.34"]),
                    ("https", "canonical.example", 443, ["93.184.216.35"]),
                    ("https", "canonical.example", 443, ["93.184.216.35"]),
                ],
            ),
            patch(
                "rss_to_kindle.fetcher._request_once", side_effect=[first, second]
            ) as request_once,
        ):
            response = safe_http_get(
                "https://original.example/article", cookies={"substack.sid": "secret"}
            )
        self.assertEqual(response.text, "ok")
        self.assertEqual(request_once.call_args_list[0].args[3], {"substack.sid": "secret"})
        self.assertEqual(request_once.call_args_list[1].args[3], {})


class ExtractionTests(unittest.TestCase):
    def test_parsed_paywall_markers_do_not_match_subscribe_button_alone(self) -> None:
        self.assertTrue(
            _is_paywall_page(
                '<div class="paywall"><a class="paywall-login">Log in</a>'
                '<h2 class="paywall-title">Subscribe to read</h2></div>'
            )
        )
        self.assertFalse(_is_paywall_page('<button class="subscribe-btn">Subscribe</button>'))
        self.assertFalse(_is_paywall_page("<script>const note = 'paywall';</script>"))

    def test_subscription_wall_has_actionable_safe_error(self) -> None:
        wall = (
            "<html><head><title>Post</title></head><body>"
            '<div class="paywall"><a class="paywall-login">Log in</a>'
            '<h2 class="paywall-title">Subscribe to read</h2></div></body></html>'
        )
        config = {
            "substack_session_cookie": "secret-cookie",
            "substack_connect_cookies": {},
        }
        with patch(
            "rss_to_kindle.extractor.safe_http_get",
            return_value=fetched_page(wall, url="https://writer.substack.com/p/locked"),
        ):
            with self.assertRaises(SubscriptionRequiredError) as caught:
                extract_url_article("https://writer.substack.com/p/locked", config)
        self.assertTrue(caught.exception.had_cookie)
        self.assertIn("may have expired", str(caught.exception))
        self.assertNotIn("secret-cookie", str(caught.exception))

    def test_subscription_button_alone_does_not_block_extraction(self) -> None:
        page = PUBLIC_ARTICLE_HTML.replace(
            "<nav>Menu</nav>", '<button class="subscribe-btn">Subscribe</button>'
        )
        stages = []
        with patch("rss_to_kindle.extractor.safe_http_get", return_value=fetched_page(page)):
            article = extract_url_article(ARTICLE_URL, {}, stages.append)
        self.assertEqual(article.title, "Useful Article")
        self.assertEqual(stages, ["fetching", "extracting"])
        self.assertTrue(article.html_content.strip())

    def test_relative_cover_url_uses_final_article_url(self) -> None:
        article = extract_article(
            PUBLIC_ARTICLE_HTML.replace(
                "</head>", '<meta property="og:image" content="/images/cover.jpg"></head>'
            ),
            url=ARTICLE_URL,
        )
        self.assertEqual(
            article.cover_image_url,
            "https://writer.example.com/images/cover.jpg",
        )

    def test_failed_or_skipped_images_leave_no_external_epub_urls(self) -> None:
        raw_html = (
            '<figure><img src="http://127.0.0.1/private.png" alt="Diagram">'
            "<figcaption>Caption remains visible</figcaption></figure>"
            '<picture><source srcset="https://private.example/image.png 2x">'
            '<img src="https://public.example/image.png" alt="Second"></picture>'
        )
        with patch("rss_to_kindle.extractor.safe_http_get", side_effect=UnsafeURL("blocked")):
            cleaned, images = _download_images(raw_html, base_url=ARTICLE_URL)
        doc = html.fromstring(cleaned)
        self.assertEqual(images, {})
        self.assertFalse(doc.xpath("//img[@src or @srcset] | //source[@src or @srcset]"))
        self.assertEqual(doc.xpath("//figcaption/text()"), ["Caption remains visible"])

    def test_image_limit_strips_sources_from_unprocessed_images(self) -> None:
        raw_html = (
            '<figure><img src="https://public.example/one.png" alt="One">'
            "<figcaption>First caption</figcaption></figure>"
            '<figure><img src="https://public.example/two.png" alt="Two">'
            "<figcaption>Second caption</figcaption></figure>"
        )
        response = FetchedPage(
            200,
            httpx.Headers({"content-type": "image/png"}),
            b"image-bytes",
            "https://public.example/one.png",
        )
        with (
            patch("rss_to_kindle.extractor.MAX_ARTICLE_IMAGES", 1),
            patch("rss_to_kindle.extractor.safe_http_get", return_value=response),
        ):
            cleaned, images = _download_images(raw_html)
        doc = html.fromstring(cleaned)
        self.assertEqual(len(images), 1)
        self.assertEqual(doc.xpath("//img/@src"), [f"images/{next(iter(images))}"])
        self.assertEqual(doc.xpath("//figcaption/text()"), ["First caption", "Second caption"])

    def test_epub_escapes_article_metadata_as_xml_text(self) -> None:
        article = Article(
            "Research & <Practice>",
            "A & B",
            "2026 & now",
            "<p>Body &amp; more</p>",
        )
        with patch("rss_to_kindle.kindle._download_cover_image", return_value=None):
            data = build_epub(article)
        with ZipFile(BytesIO(data)) as book:
            chapter = book.read("EPUB/article.xhtml")
        etree.fromstring(chapter)
        doc = html.fromstring(chapter)
        self.assertEqual(doc.xpath("//h1/text()"), ["Research & <Practice>"])
        byline = doc.xpath("//p[em]")[0].text_content()
        self.assertIn("A & B", byline)
        self.assertIn("2026 & now", byline)


class PipelineTests(unittest.TestCase):
    def test_send_emits_stages_and_returns_article(self) -> None:
        article = Article("Title", "Writer", "", "<p>Content</p>")
        config = {
            "kindle_email": "reader@kindle.com",
            "sender_email": "sender@example.com",
            "sender_password": "secret",
        }
        stages = []
        with (
            patch("rss_to_kindle.pipeline.extract_url_article", return_value=article),
            patch("rss_to_kindle.pipeline.send_to_kindle") as send,
        ):
            returned = send_url_to_kindle(ARTICLE_URL, config, stages.append)
        self.assertIs(returned, article)
        self.assertEqual(stages, ["sending"])
        send.assert_called_once()

    def test_delivery_failure_is_safe_and_marks_delivery_unknown(self) -> None:
        article = Article("Title", "Writer", "", "<p>Content</p>")
        config = {
            "kindle_email": "reader@kindle.com",
            "sender_email": "sender@example.com",
            "sender_password": "never-print-this-secret",
        }
        with (
            patch("rss_to_kindle.pipeline.extract_url_article", return_value=article),
            patch(
                "rss_to_kindle.pipeline.send_to_kindle",
                side_effect=RuntimeError("never-print-this-secret"),
            ),
        ):
            with self.assertRaises(DeliveryError) as caught:
                send_url_to_kindle(ARTICLE_URL, config)
        self.assertEqual(caught.exception.delivery_state, "unknown")
        self.assertNotIn("never-print-this-secret", caught.exception.user_message)

    def test_generic_403_is_access_error_not_stale_authentication(self) -> None:
        page = fetched_page("<html><body>Forbidden</body></html>", status_code=403)
        with (
            patch("rss_to_kindle.extractor.safe_http_get", return_value=page),
            patch("rss_to_kindle.pipeline.send_to_kindle") as send,
        ):
            with self.assertRaises(AccessError) as caught:
                send_url_to_kindle(ARTICLE_URL, {})
        self.assertIn("HTTP 403", caught.exception.user_message)
        self.assertNotIn("saved login may have expired", caught.exception.user_message)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
