import csv
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

from releasenote.config import WorkspaceConfig
from releasenote.server import create_server
from releasenote.service import build_draft, sample


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

    def _post_status(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

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

    def test_version_is_0_4_0(self):
        status, body = self._get("/api/version")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], "0.4.0")

    def test_config_sample_endpoint(self):
        status, body = self._get("/api/config/sample")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        config = WorkspaceConfig.from_dict(payload)
        self.assertTrue(config.extra_vague_terms)
        self.assertEqual(payload, config.to_dict())

    def test_generate_applies_extra_term_and_severity_override(self):
        content = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Blazing sync,Sync is blazing fast,Quick,A\n"
        )
        status, result = self._post(
            "/api/generate",
            {
                "content": content,
                "format": "csv",
                "config": {
                    "extra_vague_terms": ["blazing"],
                    "severity_overrides": {"vague_language": "high"},
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertFalse(result["qa"]["publishable"])
        vague = [f for f in result["qa"]["flags"] if f["code"] == "vague_language"]
        self.assertEqual(len(vague), 1)
        self.assertEqual(vague[0]["severity"], "high")

    def test_generate_required_section_missing(self):
        content = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Fix login,Login no longer fails,Reliable access,A\n"
        )
        status, result = self._post(
            "/api/generate",
            {
                "content": content,
                "format": "csv",
                "config": {"required_sections": ["breaking"]},
            },
        )
        self.assertEqual(status, 200)
        missing = [
            f
            for f in result["qa"]["flags"]
            if f["code"] == "missing_required_section"
        ]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["severity"], "medium")
        self.assertEqual(missing[0]["entry_id"], "")

    def test_generate_rejects_unknown_config_key(self):
        status, result = self._post_status(
            "/api/generate",
            {"content": sample("csv")["content"], "config": {"nope": []}},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_generate_rejects_non_list_config_field(self):
        status, result = self._post_status(
            "/api/generate",
            {
                "content": sample("csv")["content"],
                "config": {"extra_vague_terms": "blazing"},
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_generate_rejects_invalid_severity(self):
        status, result = self._post_status(
            "/api/generate",
            {
                "content": sample("csv")["content"],
                "config": {"severity_overrides": {"vague_language": "critical"}},
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_generate_rejects_non_object_config(self):
        status, result = self._post_status(
            "/api/generate",
            {"content": sample("csv")["content"], "config": ["high"]},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", result)

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

    def test_diff_endpoint_happy_path(self):
        previous = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Fast and reliable,SRC-A,https://a\n"
        )
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Reliable and fast,,\n"
            "B,fix,Beta,Beta description,Gamma value,SRC-B,\n"
        )
        status, result = self._post(
            "/api/diff",
            {
                "previous": previous,
                "current": current,
                "format": "csv",
                "title": "Release 5",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["title"], "Release 5")
        for key in ("markdown", "qa", "checklist", "config", "diff"):
            self.assertIn(key, result)
        self.assertEqual(
            set(result["diff"]),
            {
                "added",
                "changed",
                "removed",
                "unchanged",
                "repeated_claims",
                "regressions",
            },
        )

    def test_diff_flags_lost_source_regression(self):
        previous = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Fast and reliable,SRC-A,https://a\n"
        )
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Reliable and fast,,\n"
        )
        status, result = self._post(
            "/api/diff",
            {"previous": previous, "current": current, "format": "csv"},
        )
        self.assertEqual(status, 200)
        unsourced = [
            f
            for f in result["qa"]["flags"]
            if f["code"] == "regression_unsourced"
        ]
        self.assertEqual(len(unsourced), 1)
        self.assertEqual(unsourced[0]["severity"], "high")
        self.assertTrue(
            any(item["id"] == "A" for item in result["diff"]["regressions"])
        )

    def test_diff_honours_severity_override(self):
        previous = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Fast and reliable,SRC-A,https://a\n"
        )
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Reliable and fast,,\n"
        )
        status, result = self._post(
            "/api/diff",
            {
                "previous": previous,
                "current": current,
                "format": "csv",
                "config": {
                    "severity_overrides": {"regression_unsourced": "low"}
                },
            },
        )
        self.assertEqual(status, 200)
        flag = next(
            f
            for f in result["qa"]["flags"]
            if f["code"] == "regression_unsourced"
        )
        self.assertEqual(flag["severity"], "low")

    def test_diff_rejects_missing_current(self):
        status, result = self._post_status(
            "/api/diff",
            {"previous": sample("csv")["content"], "format": "csv"},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_diff_rejects_invalid_config(self):
        status, result = self._post_status(
            "/api/diff",
            {
                "previous": sample("csv")["content"],
                "current": sample("csv")["content"],
                "config": {"nope": []},
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_diff_rejects_malformed_body(self):
        request = urllib.request.Request(
            self.base + "/api/diff",
            data=b"not-json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 400)

    def test_diff_rejects_oversized_body(self):
        huge = "x" * (2 * 1024 * 1024 + 10)
        request = urllib.request.Request(
            self.base + "/api/diff",
            data=json.dumps(
                {"previous": huge, "current": huge, "format": "csv"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(ctx.exception.code, 413)

    def test_diff_rejects_unparseable_content(self):
        status, result = self._post_status(
            "/api/diff",
            {
                "previous": "foo,bar\n1,2\n",
                "current": "foo,bar\n1,2\n",
                "format": "csv",
            },
        )
        self.assertEqual(status, 422)
        self.assertIn("error", result)

    def _get_export(self, path, params):
        query = urllib.parse.urlencode(params)
        return self._get(path + "?" + query)

    def _get_export_raw(self, path, params):
        query = urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(
                self.base + path + "?" + query, timeout=5
            ) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_export_qa_csv_endpoint(self):
        content = sample("csv")["content"]
        query = urllib.parse.urlencode({"content": content, "format": "csv"})
        with urllib.request.urlopen(
            self.base + "/api/export/qa.csv?" + query, timeout=5
        ) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.headers["Content-Type"].startswith("text/csv"))
            self.assertIn("attachment", resp.headers["Content-Disposition"])
            body = resp.read().decode("utf-8")
        self.assertEqual(body.splitlines()[0], "entry_id,code,severity,message")

    def test_export_checklist_csv_endpoint(self):
        content = sample("csv")["content"]
        status, headers, body = self._get_export_raw(
            "/api/export/checklist.csv", {"content": content, "format": "csv"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertEqual(body.decode("utf-8").splitlines()[0], "label,status,detail")

    def test_export_draft_json_endpoint_matches_build_draft(self):
        content = sample("csv")["content"]
        status, headers, body = self._get_export_raw(
            "/api/export/draft.json", {"content": content, "format": "csv"}
        )
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        exported = json.loads(body.decode("utf-8"))
        expected = build_draft(content, "csv")
        self.assertEqual(exported["qa"], expected["qa"])
        self.assertEqual(exported["checklist"], expected["checklist"])
        self.assertEqual(exported["entries"], expected["entries"])

    def test_export_diff_csv_endpoint(self):
        previous = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Fast and reliable,SRC-A,https://a\n"
        )
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Reliable and fast,,\n"
            "B,fix,Beta,Beta description,Gamma value,SRC-B,\n"
        )
        query = urllib.parse.urlencode(
            {"previous": previous, "current": current, "format": "csv"}
        )
        with urllib.request.urlopen(
            self.base + "/api/export/diff.csv?" + query, timeout=5
        ) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.headers["Content-Type"].startswith("text/csv"))
            body = resp.read().decode("utf-8")
        rows = list(csv.reader(io.StringIO(body)))
        self.assertEqual(rows[0], ["category", "id", "title", "type", "reason"])
        self.assertGreater(len(rows), 1)

    def test_export_diff_csv_requires_previous_and_current(self):
        status, _headers, body = self._get_export_raw("/api/export/diff.csv", {})
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)

        status, _headers, body = self._get_export_raw(
            "/api/export/diff.csv", {"current": sample("csv")["content"]}
        )
        self.assertEqual(status, 400)

    def test_export_diff_csv_unparseable_is_422(self):
        status, _headers, body = self._get_export_raw(
            "/api/export/diff.csv",
            {"previous": "foo,bar\n1,2\n", "current": "foo,bar\n1,2\n", "format": "csv"},
        )
        self.assertEqual(status, 422)
        self.assertIn(b"error", body)

    def test_export_qa_csv_missing_content_is_400(self):
        status, _headers, body = self._get_export_raw(
            "/api/export/qa.csv", {"format": "csv"}
        )
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)

    def test_export_qa_csv_unparseable_is_422(self):
        status, _headers, _body = self._get_export_raw(
            "/api/export/qa.csv", {"content": "foo,bar\n1,2\n", "format": "csv"}
        )
        self.assertEqual(status, 422)

    def test_export_invalid_format_is_400(self):
        status, _headers, _body = self._get_export_raw(
            "/api/export/qa.csv",
            {"content": sample("csv")["content"], "format": "xml"},
        )
        self.assertEqual(status, 400)

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

    def test_invalid_config_is_not_stored(self):
        with urllib.request.urlopen(
            self.base + "/api/history", timeout=5
        ) as resp:
            before = json.loads(resp.read().decode("utf-8"))["drafts"]

        request = urllib.request.Request(
            self.base + "/api/generate",
            data=json.dumps(
                {
                    "content": sample("csv")["content"],
                    "format": "csv",
                    "config": {"bogus_key": []},
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 400)
        error = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertIn("error", error)

        with urllib.request.urlopen(
            self.base + "/api/history", timeout=5
        ) as resp:
            after = json.loads(resp.read().decode("utf-8"))["drafts"]
        self.assertEqual(len(after), len(before))

    def test_diff_invalid_config_is_not_stored(self):
        with urllib.request.urlopen(
            self.base + "/api/history", timeout=5
        ) as resp:
            before = json.loads(resp.read().decode("utf-8"))["drafts"]

        request = urllib.request.Request(
            self.base + "/api/diff",
            data=json.dumps(
                {
                    "previous": sample("csv")["content"],
                    "current": sample("csv")["content"],
                    "format": "csv",
                    "config": {"bogus_key": []},
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 400)

        with urllib.request.urlopen(
            self.base + "/api/history", timeout=5
        ) as resp:
            after = json.loads(resp.read().decode("utf-8"))["drafts"]
        self.assertEqual(len(after), len(before))

    def test_unknown_draft_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/api/history/nope", timeout=5)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
