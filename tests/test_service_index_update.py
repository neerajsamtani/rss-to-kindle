import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/update-service-index.py"
SPEC = importlib.util.spec_from_file_location("update_service_index", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SERVICE_INDEX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVICE_INDEX)


class ServiceIndexUpdateTests(unittest.TestCase):
    def test_adds_card_before_manage_group_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            services = Path(temporary) / "services.json"
            services.write_text(
                "[\n"
                '  {"name":"Books","group":"Your library"},\n'
                '  {"name":"Dagu","group":"Manage your server"}\n'
                "]\n"
            )

            self.assertTrue(SERVICE_INDEX.update_service_index(services))
            parsed = json.loads(services.read_text())
            self.assertEqual(parsed[1], SERVICE_INDEX.ENTRY)
            self.assertEqual(parsed[2]["name"], "Dagu")
            self.assertFalse(SERVICE_INDEX.update_service_index(services))
            self.assertTrue(services.with_name("services.json.pre-rss-to-kindle").exists())

    def test_refuses_different_existing_card(self):
        with tempfile.TemporaryDirectory() as temporary:
            services = Path(temporary) / "services.json"
            services.write_text(json.dumps([{"name": "RSS to Kindle", "url": "/wrong/"}]))

            with self.assertRaisesRegex(ValueError, "different settings"):
                SERVICE_INDEX.update_service_index(services)


if __name__ == "__main__":
    unittest.main()
