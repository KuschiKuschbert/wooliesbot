import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import api
import keep_sync


class KeepSyncNormalizeTests(unittest.TestCase):
    def test_normalize_shopping_list_filters_bad_rows_and_clamps_qty(self):
        raw = [
            {"name": "Milk 2L", "qty": 2},
            {"name": "  Bread  ", "qty": 0},
            {"name": "", "qty": 5},
            {"qty": 3},
            "not-a-dict",
            {"name": "Eggs", "qty": "not-a-number"},
        ]

        got = keep_sync._normalize_shopping_list(raw)

        self.assertEqual(
            got,
            [
                {"name": "Milk 2L", "qty": 2},
                {"name": "Bread", "qty": 1},
                {"name": "Eggs", "qty": 1},
            ],
        )


class ApiSyncEndpointTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.call_event = threading.Event()

        def fake_run_keep_sync(shopping_list=None):
            self.calls.append(shopping_list)
            self.call_event.set()

        self._patcher = mock.patch.object(api, "run_keep_sync", side_effect=fake_run_keep_sync)
        self._patcher.start()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), api.LocalBotHandler)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self._patcher.stop()

    def _post_json(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))

    def test_post_sync_starts_background_keep_sync_with_cleaned_payload(self):
        code, data = self._post_json(
            "/sync",
            {
                "shoppingList": [
                    {"name": "Milk", "qty": 2},
                    {"name": "  ", "qty": 3},
                    {"name": "Bread", "qty": 0},
                ]
            },
        )

        self.assertEqual(code, 200)
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("items"), 2)

        self.assertTrue(self.call_event.wait(timeout=2), "run_keep_sync was not called")
        self.assertEqual(
            self.calls[0],
            [{"name": "Milk", "qty": 2}, {"name": "Bread", "qty": 1}],
        )

    def test_post_sync_rejects_invalid_payload(self):
        req = urllib.request.Request(
            f"{self.base_url}/sync",
            data=json.dumps({"shoppingList": "bad"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=3)

        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertEqual(body.get("status"), "bad_request")
        self.assertEqual(self.calls, [])

    def test_get_sync_still_uses_legacy_no_payload_call(self):
        with urllib.request.urlopen(f"{self.base_url}/sync", timeout=3) as resp:
            code = resp.getcode()
            payload = json.loads(resp.read().decode("utf-8"))

        self.assertEqual(code, 200)
        self.assertEqual(payload.get("status"), "success")
        self.assertTrue(self.call_event.wait(timeout=2), "run_keep_sync was not called")
        self.assertEqual(self.calls[0], None)


if __name__ == "__main__":
    unittest.main()
