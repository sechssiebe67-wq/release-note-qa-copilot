import csv
import io
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

_BASE_CSV = (
    "id,type,title,description,benefit,source\n"
    "A,fix,Fix login,Login no longer fails,Reliable access,A\n"
)
_NEXT_CSV = _BASE_CSV + (
    "B,feature,Bulk export,Export every report,"
    "One-click export saves time,B\n"
)


class _VersionServerMixin:
    @classmethod
    def _start(cls):
        cls.server, host, port = create_server("127.0.0.1", 0)
        cls.base = "http://127.0.0.1:{}".format(port)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()

    @classmethod
    def _stop(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def _request(self, path, payload=None, method="GET"):
        data = (
            json.dumps(payload).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {"Content-Type": "application/json"} if data else {}
        request = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def _get_json(self, path):
        status, _headers, body = self._request(path)
        return status, json.loads(body.decode("utf-8"))

    def _post_json(self, path, payload):
        status, _headers, body = self._request(path, payload, "POST")
        return status, json.loads(body.decode("utf-8"))

    def _key(self):
        return self.id().split(".")[-1].replace("_", "-")

    def _generate(self, key, content=_BASE_CSV):
        status, result = self._post_json(
            "/api/generate",
            {"content": content, "format": "csv", "draft_key": key},
        )
        self.assertEqual(status, 200)
        return result


class ServerVersionTests(_VersionServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"DATA_DIR": cls.tmp.name})
        cls.env.start()
        cls._start()

    @classmethod
    def tearDownClass(cls):
        cls._stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def _version_lines(self):
        path = os.path.join(self.tmp.name, "versions.jsonl")
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_generate_creates_numbered_versions(self):
        key = self._key()
        first = self._generate(key)
        second = self._generate(key)
        self.assertEqual(first["draft_key"], key)
        self.assertEqual(first["version"], 1)
        self.assertEqual(second["version"], 2)

        lines = [
            line for line in self._version_lines() if line["draft_key"] == key
        ]
        self.assertEqual([line["version"] for line in lines], [1, 2])
        seqs = [line["seq"] for line in lines]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))
        for line in lines:
            for field in ("draft_key", "version", "draft_id", "created_at"):
                self.assertIn(field, line)

    def test_list_versions_ascending(self):
        key = self._key()
        self._generate(key)
        self._generate(key)
        status, listing = self._get_json("/api/versions/" + key)
        self.assertEqual(status, 200)
        self.assertEqual(listing["draft_key"], key)
        self.assertEqual(
            [entry["version"] for entry in listing["versions"]], [1, 2]
        )

    def test_list_unknown_key_empty(self):
        status, listing = self._get_json("/api/versions/no-such-key")
        self.assertEqual(status, 200)
        self.assertEqual(listing["versions"], [])

    def test_malformed_key_is_400(self):
        status, body = self._get_json("/api/versions/UPPER")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_get_version_payload(self):
        key = self._key()
        created = self._generate(key)
        status, payload = self._get_json("/api/versions/" + key + "/1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["draft_key"], key)
        self.assertEqual(payload["entries"], created["entries"])

    def test_get_unknown_version_is_404(self):
        key = self._key()
        self._generate(key)
        status, body = self._get_json("/api/versions/" + key + "/9")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def _two_versions(self, key):
        self._generate(key, _BASE_CSV)
        self._generate(key, _NEXT_CSV)

    def test_diff_versions_endpoint(self):
        key = self._key()
        self._two_versions(key)
        status, report = self._post_json(
            "/api/versions/diff",
            {
                "draft_key": key,
                "previous_version": 1,
                "current_version": 2,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(report["previous_version"], 1)
        self.assertEqual(report["current_version"], 2)
        for category in (
            "added",
            "changed",
            "removed",
            "unchanged",
            "repeated_claims",
            "regressions",
        ):
            self.assertIn(category, report["diff"])
        self.assertEqual(
            [item["id"] for item in report["diff"]["added"]], ["B"]
        )

    def test_diff_invalid_body_is_400(self):
        key = self._key()
        self._generate(key)
        status, body = self._post_json(
            "/api/versions/diff",
            {"draft_key": key, "previous_version": 1},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_diff_unknown_version_is_404(self):
        key = self._key()
        self._generate(key)
        status, body = self._post_json(
            "/api/versions/diff",
            {
                "draft_key": key,
                "previous_version": 1,
                "current_version": 9,
            },
        )
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_rollback_sets_rolled_back_from(self):
        key = self._key()
        self._two_versions(key)
        status, payload = self._post_json(
            "/api/versions/rollback", {"draft_key": key, "version": 1}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["rolled_back_from"], 2)

    def test_rollback_unknown_version_is_404(self):
        key = self._key()
        self._generate(key)
        status, body = self._post_json(
            "/api/versions/rollback", {"draft_key": key, "version": 9}
        )
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_rollback_missing_key_is_400(self):
        status, body = self._post_json(
            "/api/versions/rollback", {"version": 1}
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_export_versions_csv(self):
        key = self._key()
        self._generate(key)
        self._generate(key)
        status, headers, body = self._request(
            "/api/export/versions.csv?draft_key=" + key
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        self.assertEqual(rows[0], ["version", "draft_id", "created_at"])
        self.assertEqual([row[0] for row in rows[1:]], ["1", "2"])

    def test_export_versions_json(self):
        key = self._key()
        self._generate(key)
        status, headers, body = self._request(
            "/api/export/versions.json?draft_key=" + key
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("attachment", headers["Content-Disposition"])
        exported = json.loads(body.decode("utf-8"))
        self.assertEqual(exported["draft_key"], key)
        self.assertEqual(len(exported["versions"]), 1)

    def test_export_version_diff_csv(self):
        key = self._key()
        self._two_versions(key)
        status, headers, body = self._request(
            "/api/export/version-diff.csv?draft_key={}&previous_version=1"
            "&current_version=2".format(key)
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        self.assertEqual(
            rows[0],
            ["version", "category", "id", "title", "type", "reason"],
        )
        added = [row for row in rows[1:] if row[1] == "added"]
        self.assertEqual([row[2] for row in added], ["B"])
        self.assertTrue(all(row[0] == "2" for row in rows[1:]))


class ServerVersionNoHistoryTests(_VersionServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {})
        cls.env.start()
        os.environ.pop("DATA_DIR", None)
        cls._start()

    @classmethod
    def tearDownClass(cls):
        cls._stop()
        cls.env.stop()

    def _assert_409(self, status, body):
        self.assertEqual(status, 409)
        self.assertIn("DATA_DIR", json.loads(body.decode("utf-8"))["error"])

    def test_list_without_history_is_409(self):
        status, _headers, body = self._request("/api/versions/rel")
        self._assert_409(status, body)

    def test_get_version_without_history_is_409(self):
        status, _headers, body = self._request("/api/versions/rel/1")
        self._assert_409(status, body)

    def test_diff_without_history_is_409(self):
        status, _headers, body = self._request(
            "/api/versions/diff",
            {"draft_key": "rel", "previous_version": 1, "current_version": 2},
            "POST",
        )
        self._assert_409(status, body)

    def test_rollback_without_history_is_409(self):
        status, _headers, body = self._request(
            "/api/versions/rollback",
            {"draft_key": "rel", "version": 1},
            "POST",
        )
        self._assert_409(status, body)

    def test_exports_without_history_are_409(self):
        for path in (
            "/api/export/versions.csv?draft_key=rel",
            "/api/export/versions.json?draft_key=rel",
            "/api/export/version-diff.csv?draft_key=rel"
            "&previous_version=1&current_version=2",
        ):
            status, _headers, body = self._request(path)
            self._assert_409(status, body)

    def test_generate_with_draft_key_still_works(self):
        status, _headers, body = self._request(
            "/api/generate",
            {
                "content": sample("csv")["content"],
                "format": "csv",
                "draft_key": "rel",
            },
            "POST",
        )
        self.assertEqual(status, 200)
        result = json.loads(body.decode("utf-8"))
        self.assertNotIn("version", result)


if __name__ == "__main__":
    unittest.main()
