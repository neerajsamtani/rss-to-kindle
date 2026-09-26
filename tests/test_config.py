import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rss_to_kindle import config


class EnvWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.env_path = root / ".env"
        self.example_path = root / ".env.example"
        self.paths = (
            patch.object(config, "ENV_PATH", self.env_path),
            patch.object(config, "ENV_EXAMPLE_PATH", self.example_path),
        )
        for path in self.paths:
            path.start()
            self.addCleanup(path.stop)

    def test_existing_env_updates_key_and_preserves_other_lines(self) -> None:
        self.env_path.write_text("# keep me\nKINDLE_EMAIL=old@example.com\nFEEDS=https://a.test\n")

        config.save_kindle_email_to_env("new@kindle.com")

        self.assertEqual(
            self.env_path.read_text(),
            "# keep me\nKINDLE_EMAIL=new@kindle.com\nFEEDS=https://a.test\n",
        )

    def test_existing_env_appends_missing_cookie_key(self) -> None:
        self.env_path.write_text("# comment\nFEEDS=https://a.test\n")

        config.save_cookie_to_env("secret", 123)

        self.assertEqual(
            self.env_path.read_text(),
            '# comment\nFEEDS=https://a.test\nSUBSTACK_SESSION_COOKIE={"value": "secret", "expires": 123}\n',
        )

    def test_kindle_first_run_uses_example_placeholder(self) -> None:
        self.example_path.write_text("# template\nKINDLE_EMAIL=name@kindle.com\nFEEDS=\n")

        config.save_kindle_email_to_env("reader@kindle.com")

        self.assertEqual(
            self.env_path.read_text(),
            "# template\nKINDLE_EMAIL=reader@kindle.com\nFEEDS=\n",
        )

    def test_cookie_first_run_replaces_template_key(self) -> None:
        self.example_path.write_text(
            '# template\nSUBSTACK_SESSION_COOKIE={"value": "s%3A..."}\nFEEDS=\n'
        )

        config.save_cookie_to_env("actual", 456)

        self.assertEqual(
            self.env_path.read_text(),
            '# template\nSUBSTACK_SESSION_COOKIE={"value": "actual", "expires": 456}\nFEEDS=\n',
        )

    def test_cookie_template_without_key_is_preserved_without_appending(self) -> None:
        self.example_path.write_text("# template\nFEEDS=\n")

        config.save_cookie_to_env("actual")

        self.assertEqual(self.env_path.read_text(), "# template\nFEEDS=\n")

    def test_connect_cookie_first_run_does_not_copy_example(self) -> None:
        self.example_path.write_text("# template\nFEEDS=\n")
        cookies = {"newsletter.test": {"value": "connect", "expires": 789}}

        config.save_connect_cookies_to_env(cookies)

        self.assertEqual(
            self.env_path.read_text(),
            f"SUBSTACK_CONNECT_COOKIES={json.dumps(cookies)}\n",
        )

    def test_missing_example_creates_standalone_key(self) -> None:
        config.save_kindle_email_to_env("reader@kindle.com")

        self.assertEqual(self.env_path.read_text(), "KINDLE_EMAIL=reader@kindle.com\n")


if __name__ == "__main__":
    unittest.main()
