import unittest
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from lxml import etree, html

from rss_to_kindle.extractor import _simplify_headers, extract_article
from rss_to_kindle.kindle import build_epub

URL = "https://example.com/posts/shipping/"
PROSE = (
    "Building and publishing require practice, patience, and persistence. "
    "This paragraph supplies enough article text for readability to select the main content. "
)
# Reduced reproduction of the markup in seangoedecke.com/grit-your-teeth-and-ship-it/.
PAGE = f"""<html><head><title>Shipping</title></head><body>
<nav class="menu"><ul><li><a href="/home">Home</a></li></ul></nav>
<article><section><p>{PROSE * 3}</p>
<h3 id="writing">Writing<a href="#writing" class="heading-anchor after">
<svg width="16" height="16"><path d="M0 0" /></svg></a></h3>
<p>Examples that readers liked:</p>
<ul><li><a href="/one">The first example of a popular post</a></li>
<li><a href="/two">The second example of a popular post</a></li>
<li><a href="/three">The third example of a popular post</a></li></ul>
<p>Examples that readers overlooked:</p>
<ul><li><a href="/four">The first overlooked example post</a></li>
<li><a href="/five">The second overlooked example post</a></li>
<li><a href="/six">The third overlooked example post</a></li></ul>
<p>{PROSE}A supporting note<sup id="fnref-1"><a href="#fn-1"
class="footnote-ref">1</a></sup> continues here.</p>
<div class="footnotes"><hr><ol><li id="fn-1"><p>A writer can misjudge which
of their works readers will enjoy, even after years of experience.</p>
<a href="#fnref-1" class="footnote-backref">↩</a></li></ol></div>
<p>{PROSE * 3}</p></section></article>
<footer class="footer"><ul><li>Footer navigation</li></ul></footer>
</body></html>"""


class ExtractionTests(unittest.TestCase):
    def test_article_survives_epub_conversion(self) -> None:
        article = extract_article(PAGE, url=URL)
        with patch("rss_to_kindle.kindle._download_cover_image", return_value=None):
            data = build_epub(article)
        with ZipFile(BytesIO(data)) as book:
            chapter = book.read("EPUB/article.xhtml")
        etree.fromstring(chapter)  # EPUB chapter must remain well-formed XML.
        doc = html.fromstring(chapter)
        self.assertEqual(len(doc.xpath("//ul/li")), 6)
        self.assertEqual(doc.xpath('//h3[@id="writing"]/text()'), ["Writing"])
        self.assertFalse(doc.xpath("//svg"))
        self.assertFalse(doc.xpath("//*[@data-kindle-preserved]"))
        self.assertIn("A writer can misjudge", doc.text_content())
        self.assertEqual(doc.xpath("//sup/a/@href"), ["#fn-1"])
        self.assertEqual(doc.xpath('//*[@id="fn-1"]//a/@href'), ["#fnref-1"])
        self.assertEqual(
            doc.xpath("//ul/li/a/@href"),
            [
                f"https://example.com/{name}"
                for name in ("one", "two", "three", "four", "five", "six")
            ],
        )
        self.assertNotIn("Footer navigation", doc.text_content())
        self.assertNotIn("Home", doc.text_content())

    def test_nested_short_lists_and_note_roles(self) -> None:
        page = PAGE.replace(
            "<p>Examples that readers liked:</p>",
            '<ol start="3"><li>First<ul><li>Nested</li></ul></li><li>Second</li></ol>'
            '<p>A note<a role="doc-noteref" href="#note">2</a>.</p>'
            '<aside role="doc-endnote" id="note">Short note.</aside>',
        )
        doc = html.fromstring(extract_article(page, url=URL).html_content)
        self.assertEqual(doc.xpath('//ol[@start="3"]/li/ul/li/text()'), ["Nested"])
        self.assertEqual(doc.xpath('//*[@id="note"]/text()'), ["Short note."])
        self.assertEqual(doc.xpath('//a[@role="doc-noteref"]/@href'), ["#note"])

    def test_heading_cleanup_keeps_text_links_and_content_images(self) -> None:
        raw = """<div><h2 class="header-anchor-post" id="section">Before
        <span><span class="header-anchor-parent"><button>Copy</button></span>After</span>
        <a href="/reference">Reference</a><a href="#section"><img src="icon.png"></a>Tail
        </h2><p><img src="diagram.png"><svg width="100"></svg></p></div>"""
        doc = html.fromstring(_simplify_headers(raw))
        self.assertIn("After", doc.text_content())
        self.assertIn("Tail", doc.text_content())
        self.assertNotIn("Copy", doc.text_content())
        self.assertEqual(doc.xpath("//h2/@id"), ["section"])
        self.assertEqual(doc.xpath("//a/@href"), ["/reference"])
        self.assertEqual(doc.xpath("//img/@src"), ["diagram.png"])
        self.assertEqual(len(doc.xpath("//svg")), 1)

    def test_preserved_markup_is_cleaned(self) -> None:
        page = PAGE.replace("The first example", "<script>alert(1)</script>The first example")
        doc = html.fromstring(extract_article(page).html_content)
        self.assertFalse(doc.xpath("//script"))
        self.assertEqual(len(doc.xpath("//ul/li")), 6)


if __name__ == "__main__":
    unittest.main()
