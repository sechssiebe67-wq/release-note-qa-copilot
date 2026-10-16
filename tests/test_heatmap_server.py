"""Offline HTTP tests for the risk-heatmap endpoints.

Covers ``GET /api/heatmap/sample``, ``POST /api/heatmap`` and the
``GET /api/export/heatmap.csv`` / ``.json`` downloads, including their status
codes, content types and attachment headers. No network access is required.
"""

import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from releasenote.server import create_server
from releasenote.service import sample_heatmap


class HeatmapServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, host, port = create_server("127.0.0.1", 0)
        cls.base = "http://127.0.0.1:{}".format(port)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def _get_raw(self, path, params=None):
        url = self.base + path
        if params is not None:
            url += "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def _post(self, path, payload):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_sample_endpoint(self):
        status, _headers, body = self._get_raw("/api/heatmap/sample")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["format"], "csv")
        self.assertTrue(payload["content"].strip())

    def test_post_heatmap_returns_report(self):
        status, result = self._post("/api/heatmap", sample_heatmap())
        self.assertEqual(status, 200)
        for key in (
            "sections",
            "entries",
            "codes",
            "total_weighted_risk",
            "hottest_section",
            "recommendation",
        ):
            self.assertIn(key, result)
        self.assertIn(result["recommendation"]["decision"], ("ship", "hold"))

    def test_post_heatmap_missing_content_400(self):
        status, result = self._post("/api/heatmap", {"format": "csv"})
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_post_heatmap_unknown_profile_400(self):
        payload = sample_heatmap()
        payload["profile"] = "does-not-exist"
        status, result = self._post("/api/heatmap", payload)
        self.assertEqual(status, 400)
        self.assertIn("profile", result["error"])

    def test_post_heatmap_invalid_config_400(self):
        payload = sample_heatmap()
        payload["config"] = {"bogus": 1}
        status, _result = self._post("/api/heatmap", payload)
        self.assertEqual(status, 400)

    def test_post_heatmap_unparseable_content_422(self):
        status, _result = self._post(
            "/api/heatmap", {"content": "foo,bar\n1,2\n", "format": "csv"}
        )
        self.assertEqual(status, 422)

    def test_post_heatmap_oversized_body_413(self):
        huge = "x" * (2 * 1024 * 1024 + 10)
        request = urllib.request.Request(
            self.base + "/api/heatmap",
            data=json.dumps({"content": huge}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(ctx.exception.code, 413)

    def test_export_heatmap_csv(self):
        payload = sample_heatmap()
        status, headers, body = self._get_raw(
            "/api/export/heatmap.csv",
            {"format": payload["format"], "content": payload["content"]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("release-heatmap.csv", headers["Content-Disposition"])
        header = body.decode("utf-8").splitlines()[0]
        self.assertEqual(
            header,
            "scope,key,label,flag_count,high,medium,low,weighted_risk,heat",
        )

    def test_export_heatmap_json(self):
        payload = sample_heatmap()
        status, headers, body = self._get_raw(
            "/api/export/heatmap.json",
            {"format": payload["format"], "content": payload["content"]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(
            headers["Content-Type"].startswith("application/json")
        )
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("sections", json.loads(body.decode("utf-8")))

    def test_export_heatmap_missing_content_400(self):
        status, _headers, body = self._get_raw("/api/export/heatmap.csv", {})
        self.assertEqual(status, 400)
        self.assertIn("content", body.decode("utf-8"))

    def test_export_heatmap_unknown_profile_400(self):
        payload = sample_heatmap()
        status, _headers, _body = self._get_raw(
            "/api/export/heatmap.csv",
            {
                "format": payload["format"],
                "content": payload["content"],
                "profile": "nope",
            },
        )
        self.assertEqual(status, 400)

    def test_export_heatmap_unparseable_content_422(self):
        status, _headers, _body = self._get_raw(
            "/api/export/heatmap.csv",
            {"format": "csv", "content": "foo,bar\n1,2\n"},
        )
        self.assertEqual(status, 422)


if __name__ == "__main__":
    unittest.main()
