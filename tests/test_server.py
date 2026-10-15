import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from releasenote.server import create_server
from releasenote.service import sample


class ServerTests(unittest.TestCase):
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

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return resp.status, resp.read()

    def _post(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_healthz(self):
        status, body = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"ok")

    def test_index_served(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Release Note QA Copilot", body)

    def test_version(self):
        status, body = self._get("/api/version")
        self.assertEqual(status, 200)
        self.assertIn(b"release-note-qa-copilot", body)

    def test_sample_endpoint(self):
        status, body = self._get("/api/sample?format=json")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["format"], "json")

    def test_generate_endpoint(self):
        status, result = self._post(
            "/api/generate",
            {
                "content": sample("csv")["content"],
                "format": "csv",
                "title": "Release 9",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["title"], "Release 9")
        self.assertIn("# Release 9", result["markdown"])

    def test_generate_rejects_missing_content(self):
        request = urllib.request.Request(
            self.base + "/api/generate",
            data=json.dumps({}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 400)

    def test_generate_rejects_bad_payload(self):
        request = urllib.request.Request(
            self.base + "/api/generate",
            data=b"not-json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 400)

    def test_generate_rejects_unparseable_content(self):
        request = urllib.request.Request(
            self.base + "/api/generate",
            data=json.dumps(
                {"content": "foo,bar\n1,2\n", "format": "csv"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 422)

    def test_unknown_path_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/nope", timeout=5)
        self.assertEqual(ctx.exception.code, 404)


class ServerHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"DATA_DIR": cls.tmp.name})
        cls.env.start()
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
        cls.env.stop()
        cls.tmp.cleanup()

    def test_generated_draft_is_stored(self):
        request = urllib.request.Request(
            self.base + "/api/generate",
            data=json.dumps(
                {"content": sample("csv")["content"], "format": "csv"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        self.assertIn("draft_id", result)

        with urllib.request.urlopen(
            self.base + "/api/history", timeout=5
        ) as resp:
            listing = json.loads(resp.read().decode("utf-8"))["drafts"]
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["id"], result["draft_id"])

        with urllib.request.urlopen(
            self.base + "/api/history/" + result["draft_id"], timeout=5
        ) as resp:
            record = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(record["markdown"], result["markdown"])

    def test_unknown_draft_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/api/history/nope", timeout=5)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
