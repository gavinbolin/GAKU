"""Offline header regression tests: no API keys or live requests required."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from broll_queries import PexelsPicker
from pexels_http import PEXELS_USER_AGENT, pexels_headers
from visual_queue import fetch_pexels_photo

FAKE_KEY = "unit-test-key-not-a-real-credential"
PHOTO = {"id": 1, "src": {"large": "https://example.invalid/photo.jpg"}, "photographer": "Test", "alt": "Office coworkers"}

class PexelsHeaderTests(unittest.TestCase):
    def assert_headers(self, headers):
        self.assertEqual(headers["Authorization"], FAKE_KEY)
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["User-Agent"], PEXELS_USER_AGENT)
        self.assertTrue(headers["User-Agent"].startswith("Mozilla/5.0"))
        self.assertIn("Project-GAKU/1.0", headers["User-Agent"])

    def response(self):
        response = Mock()
        response.json.return_value = {"photos": [PHOTO]}
        return response

    def test_header_dicts_are_fresh_and_do_not_share_keys(self):
        headers = pexels_headers(FAKE_KEY)
        self.assert_headers(headers)
        headers["User-Agent"] = "changed"
        other = pexels_headers("another-fake-key")
        self.assertEqual(other["User-Agent"], PEXELS_USER_AGENT)
        self.assertEqual(other["Authorization"], "another-fake-key")

    def test_picker_sends_headers_and_keeps_cache(self):
        with patch("requests.get", return_value=self.response()) as get:
            picker = PexelsPicker(None, FAKE_KEY, per_page=1)
            result = picker.candidates("office coworkers")
            self.assertEqual(len(result), 1)
            self.assertEqual(picker.candidates("office coworkers"), result)
        get.assert_called_once()
        self.assert_headers(get.call_args.kwargs["headers"])
        self.assertEqual(get.call_args.kwargs["params"]["per_page"], 1)

    def test_legacy_client_sends_same_headers(self):
        with patch("requests.get", return_value=self.response()) as get:
            result = fetch_pexels_photo("office coworkers", pexels_key=FAKE_KEY)
        self.assertIsNotNone(result)
        get.assert_called_once()
        self.assert_headers(get.call_args.kwargs["headers"])

if __name__ == "__main__":
    unittest.main()
