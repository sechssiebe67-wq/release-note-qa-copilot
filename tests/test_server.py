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

    def test_version_is_0_14_0(self):
        status, body = self._get("/api/version")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], "0.14.0")

    def test_config_sample_endpoint(self):
        status, body = self._get("/api/config/sample")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        config = WorkspaceConfig.from_dict(payload)
        self.assertTrue(config.extra_vague_terms)
        self.assertEqual(payload, config.to_dict())

    def test_profiles_endpoint_lists_builtins(self):
        status, body = self._get("/api/profiles")
        self.assertEqual(status, 200)
        profiles = json.loads(body)["profiles"]
        self.assertGreaterEqual(len(profiles), 3)
        names = set()
        for profile in profiles:
            self.assertEqual(set(profile), {"name", "description", "config"})
            WorkspaceConfig.from_dict(profile["config"])
            names.add(profile["name"])
        self.assertTrue({"strict", "marketing-safe", "internal"} <= names)

    def test_generate_without_profile_or_config_matches_build_draft(self):
        content = sample("csv")["content"]
        status, result = self._post(
            "/api/generate", {"content": content, "format": "csv"}
        )
        self.assertEqual(status, 200)
        self.assertIsNone(result["profile"])
        self.assertIn("rules", result)
        expected = build_draft(content, "csv")
        self.assertEqual(result["qa"], expected["qa"])
        self.assertEqual(result["checklist"], expected["checklist"])
        self.assertEqual(result["markdown"], expected["markdown"])

    def test_generate_with_strict_profile(self):
        content = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Better sync,The sync is improved,"
            "A much better experience,A\n"
        )
        status, result = self._post(
            "/api/generate",
            {"content": content, "format": "csv", "profile": "strict"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["profile"], "strict")
        vague = [
            f for f in result["qa"]["flags"] if f["code"] == "vague_language"
        ]
        self.assertEqual(len(vague), 1)
        self.assertEqual(vague[0]["severity"], "high")
        self.assertFalse(result["qa"]["publishable"])
        self.assertEqual(result["rules"]["flag_counts"]["vague_language"], 1)

    def test_generate_unknown_profile_is_400(self):
        status, result = self._post_status(
            "/api/generate",
            {"content": sample("csv")["content"], "profile": "nope"},
        )
        self.assertEqual(status, 400)
        self.assertIn("nope", result["error"])

    def test_generate_layers_profile_and_config(self):
        content = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Better sync,The sync is improved,"
            "A much better experience,A\n"
        )
        status, result = self._post(
            "/api/generate",
            {
                "content": content,
                "format": "csv",
                "profile": "strict",
                "config": {
                    "extra_vague_terms": ["blazing"],
                    "severity_overrides": {"missing_source": "low"},
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["profile"], "strict")
        self.assertIn("blazing", result["config"]["extra_vague_terms"])
        self.assertIn("breaking", result["config"]["required_sections"])
        self.assertEqual(
            result["config"]["severity_overrides"]["vague_language"], "high"
        )
        self.assertEqual(
            result["config"]["severity_overrides"]["missing_source"], "low"
        )

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

    def test_diff_echoes_profile_and_rules(self):
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
                "profile": "strict",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["profile"], "strict")
        self.assertIn("rules", result)
        self.assertEqual(result["rules"]["severity_overrides"]["vague_language"], "high")

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

    VAGUE = (
        "id,type,title,description,benefit,source\n"
        "A,fix,Better sync,The sync is improved,"
        "A much better experience,A\n"
    )
    CLEAN = (
        "id,type,title,description,benefit,source\n"
        "A,fix,Fix login,Login no longer fails,Reliable access,A\n"
    )

    def test_export_rules_csv_endpoint(self):
        status, headers, body = self._get_export_raw(
            "/api/export/rules.csv", {"content": self.VAGUE, "format": "csv"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("qa-rules.csv", headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        self.assertEqual(rows[0], ["code", "severity", "count"])
        self.assertEqual(rows[1], ["vague_language", "medium", "1"])

    def test_export_rules_csv_header_only_when_nothing_fired(self):
        status, _headers, body = self._get_export_raw(
            "/api/export/rules.csv", {"content": self.CLEAN, "format": "csv"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body.decode("utf-8"), "code,severity,count\n")

    def test_export_rules_json_endpoint(self):
        status, headers, body = self._get_export_raw(
            "/api/export/rules.json", {"content": self.VAGUE, "format": "csv"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("attachment", headers["Content-Disposition"])
        summary = json.loads(body.decode("utf-8"))
        self.assertIn("effective_vague_terms", summary)
        self.assertIn("banned_phrases", summary)
        self.assertIn("required_sections", summary)
        self.assertIn("severity_overrides", summary)
        self.assertEqual(summary["flag_counts"]["vague_language"], 1)

    def test_export_rules_applies_profile(self):
        status, _headers, body = self._get_export_raw(
            "/api/export/rules.csv",
            {"content": self.VAGUE, "format": "csv", "profile": "strict"},
        )
        self.assertEqual(status, 200)
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        by_code = {row[0]: row for row in rows[1:]}
        self.assertEqual(
            by_code["vague_language"], ["vague_language", "high", "1"]
        )

    def test_export_rules_missing_content_is_400(self):
        status, _headers, body = self._get_export_raw(
            "/api/export/rules.csv", {"format": "csv"}
        )
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)

    def test_export_rules_unparseable_is_422(self):
        status, _headers, _body = self._get_export_raw(
            "/api/export/rules.json",
            {"content": "foo,bar\n1,2\n", "format": "csv"},
        )
        self.assertEqual(status, 422)

    def test_export_rules_unknown_profile_is_400(self):
        status, _headers, body = self._get_export_raw(
            "/api/export/rules.csv",
            {"content": self.CLEAN, "format": "csv", "profile": "nope"},
        )
        self.assertEqual(status, 400)
        self.assertIn(b"nope", body)

    def test_generate_includes_score_and_gate(self):
        status, result = self._post(
            "/api/generate",
            {"content": sample("csv")["content"], "format": "csv"},
        )
        self.assertEqual(status, 200)
        self.assertIn("score", result)
        self.assertIn("band", result["score"])
        self.assertIn("penalties", result["score"])
        self.assertEqual(result["gate"], {"status": "pass", "reasons": []})

    def test_diff_includes_score_and_gate(self):
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
                "config": {"min_score": 95, "max_high": 0},
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("score", result)
        self.assertEqual(result["gate"], result["score"]["gate"])
        self.assertEqual(result["gate"]["status"], "fail")
        self.assertTrue(result["gate"]["reasons"])

    def test_generate_gate_fails_below_min_score(self):
        status, result = self._post(
            "/api/generate",
            {
                "content": self.VAGUE,
                "format": "csv",
                "config": {"min_score": 95, "max_high": 0},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["gate"]["status"], "fail")
        self.assertTrue(result["gate"]["reasons"])
        self.assertEqual(result["score"]["gate"], result["gate"])

    def test_generate_rejects_invalid_gate_type(self):
        status, result = self._post_status(
            "/api/generate",
            {
                "content": sample("csv")["content"],
                "config": {"min_score": "high"},
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_generate_rejects_out_of_range_gate(self):
        status, result = self._post_status(
            "/api/generate",
            {
                "content": sample("csv")["content"],
                "config": {"min_score": 101},
            },
        )
        self.assertEqual(status, 400)

    def test_export_score_csv_endpoint(self):
        status, headers, body = self._get_export_raw(
            "/api/export/score.csv", {"content": self.VAGUE, "format": "csv"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("release-score.csv", headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        self.assertEqual(rows[0], ["code", "severity", "count", "points"])
        payload = build_draft(self.VAGUE, "csv")
        self.assertEqual(len(rows), 1 + len(payload["score"]["penalties"]))

    def test_export_score_csv_header_only_when_clean(self):
        status, _headers, body = self._get_export_raw(
            "/api/export/score.csv", {"content": self.CLEAN, "format": "csv"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body.decode("utf-8"), "code,severity,count,points\n")

    def test_export_score_json_matches_payload(self):
        status, headers, body = self._get_export_raw(
            "/api/export/score.json", {"content": self.VAGUE, "format": "csv"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("attachment", headers["Content-Disposition"])
        exported = json.loads(body.decode("utf-8"))
        self.assertEqual(exported, build_draft(self.VAGUE, "csv")["score"])

    def test_export_score_applies_profile_gate(self):
        status, _headers, body = self._get_export_raw(
            "/api/export/score.json",
            {"content": self.VAGUE, "format": "csv", "profile": "strict"},
        )
        self.assertEqual(status, 200)
        exported = json.loads(body.decode("utf-8"))
        self.assertEqual(exported["gate"]["status"], "fail")

    def test_export_score_accepts_diff_pair(self):
        previous = self.CLEAN
        current = self.VAGUE
        status, _headers, body = self._get_export_raw(
            "/api/export/score.csv",
            {"previous": previous, "current": current, "format": "csv"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body.decode("utf-8").startswith("code,severity,count,points"))

    def test_export_score_missing_content_is_400(self):
        status, _headers, body = self._get_export_raw(
            "/api/export/score.csv", {"format": "csv"}
        )
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)

    def test_export_score_unparseable_is_422(self):
        status, _headers, _body = self._get_export_raw(
            "/api/export/score.json",
            {"content": "foo,bar\n1,2\n", "format": "csv"},
        )
        self.assertEqual(status, 422)

    def test_export_score_invalid_config_is_400(self):
        status, _headers, body = self._get_export_raw(
            "/api/export/score.csv",
            {
                "content": self.CLEAN,
                "format": "csv",
                "config": json.dumps({"min_score": 500}),
            },
        )
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)

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

    def test_unknown_profile_is_not_stored(self):
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
                    "profile": "does-not-exist",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 400)
        error = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertIn("does-not-exist", error["error"])

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


class _ReviewServerMixin:
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

    def _post_review(self, payload):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + "/api/review",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _get_review(self, draft_id):
        try:
            with urllib.request.urlopen(
                self.base + "/api/review/" + draft_id, timeout=5
            ) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _generate(self):
        request = urllib.request.Request(
            self.base + "/api/generate",
            data=json.dumps(
                {"content": sample("csv")["content"], "format": "csv"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))


class ServerReviewTests(_ReviewServerMixin, unittest.TestCase):
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

    def test_review_happy_path_and_get(self):
        result = self._generate()
        self.assertIn("draft_id", result)
        draft_id = result["draft_id"]

        status, review = self._post_review(
            {
                "draft_id": draft_id,
                "status": "approved",
                "reviewer": "Ada",
                "comment": "Ship it",
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(review["draft_id"], draft_id)
        self.assertEqual(review["status"], "approved")
        self.assertEqual(review["reviewer"], "Ada")
        self.assertEqual(review["comment"], "Ship it")
        self.assertTrue(review["updated_at"])
        self.assertEqual(len(review["audit"]), 1)

        status, fetched = self._get_review(draft_id)
        self.assertEqual(status, 200)
        self.assertEqual(fetched["status"], "approved")
        self.assertEqual(len(fetched["audit"]), 1)

    def test_audit_contains_transitions_in_order(self):
        result = self._generate()
        draft_id = result["draft_id"]
        self._post_review(
            {"draft_id": draft_id, "status": "approved", "reviewer": "Ada"}
        )
        self._post_review(
            {"draft_id": draft_id, "status": "rejected", "reviewer": "Bob"}
        )
        _status, fetched = self._get_review(draft_id)
        self.assertEqual(fetched["status"], "rejected")
        self.assertEqual(
            [entry["status"] for entry in fetched["audit"]],
            ["approved", "rejected"],
        )

    def test_get_pending_without_review(self):
        result = self._generate()
        status, fetched = self._get_review(result["draft_id"])
        self.assertEqual(status, 200)
        self.assertEqual(fetched["status"], "pending")
        self.assertEqual(fetched["audit"], [])

    def test_unknown_status_is_400_and_not_audited(self):
        result = self._generate()
        draft_id = result["draft_id"]
        status, body = self._post_review(
            {"draft_id": draft_id, "status": "maybe"}
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)
        _status, fetched = self._get_review(draft_id)
        self.assertEqual(fetched["audit"], [])

    def test_missing_draft_id_is_400(self):
        status, body = self._post_review({"status": "approved"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_unknown_draft_is_404(self):
        status, body = self._post_review(
            {"draft_id": "missing", "status": "approved"}
        )
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_get_unknown_draft_is_404(self):
        status, body = self._get_review("missing")
        self.assertEqual(status, 404)
        self.assertIn("error", body)


class ServerReviewInMemoryTests(_ReviewServerMixin, unittest.TestCase):
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

    def test_review_works_without_history(self):
        status, review = self._post_review(
            {"draft_id": "ephemeral", "status": "approved", "reviewer": "Ada"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(review["status"], "approved")
        status, fetched = self._get_review("ephemeral")
        self.assertEqual(status, 200)
        self.assertEqual(fetched["status"], "approved")


class ServerShareTests(_ReviewServerMixin, unittest.TestCase):
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

    def _get_raw(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_share_json_payload(self):
        result = self._generate()
        draft_id = result["draft_id"]
        status, headers, body = self._get_raw("/api/share/" + draft_id)
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["draft_id"], draft_id)
        self.assertEqual(payload["title"], result["title"])
        self.assertEqual(payload["markdown"], result["markdown"])
        self.assertIn("publishable", payload["qa"])
        self.assertIn("counts", payload["qa"])
        self.assertIn("checklist", payload)
        self.assertIn("flag_counts", payload["rules"])
        self.assertIn("status", payload["review"])

    def test_share_unknown_id_is_404(self):
        status, headers, body = self._get_raw("/api/share/does-not-exist")
        self.assertEqual(status, 404)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("error", json.loads(body.decode("utf-8")))

    def test_share_html_page(self):
        result = self._generate()
        draft_id = result["draft_id"]
        status, headers, body = self._get_raw("/s/" + draft_id)
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn("charset=utf-8", headers["Content-Type"])
        text = body.decode("utf-8")
        self.assertIn(result["title"], text)
        self.assertIn(result["markdown"], text)
        self.assertNotIn("<script src", text)
        self.assertNotIn('rel="stylesheet"', text)

    def test_share_reflects_review_status(self):
        result = self._generate()
        draft_id = result["draft_id"]
        self._post_review(
            {"draft_id": draft_id, "status": "approved", "reviewer": "Ada"}
        )
        _status, _headers, body = self._get_raw("/api/share/" + draft_id)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["review"]["status"], "approved")


class ServerShareNoHistoryTests(_ReviewServerMixin, unittest.TestCase):
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

    def test_share_without_history_is_400(self):
        try:
            with urllib.request.urlopen(
                self.base + "/api/share/anything", timeout=5
            ) as resp:
                status = resp.status
                body = resp.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = exc.read()
        self.assertEqual(status, 400)
        payload = json.loads(body.decode("utf-8"))
        self.assertIn("error", payload)
        self.assertIn("DATA_DIR", payload["error"])


class ServerArchiveTests(_ReviewServerMixin, unittest.TestCase):
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

    def _get_json(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _get_raw(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def _tag(self, payload):
        request = urllib.request.Request(
            self.base + "/api/archive/tag",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _log_lines(self):
        path = os.path.join(self.tmp.name, "archive.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [line for line in handle if line.strip()]

    def _entry_for(self, draft_id):
        _status, listing = self._get_json("/api/archive")
        for entry in listing["drafts"]:
            if entry["draft_id"] == draft_id:
                return entry
        return None

    def test_archive_listing_has_expected_fields_and_order(self):
        ids = []
        for _ in range(3):
            ids.append(self._generate()["draft_id"])
        status, listing = self._get_json("/api/archive")
        self.assertEqual(status, 200)
        for draft_id in ids:
            entry = next(d for d in listing["drafts"] if d["draft_id"] == draft_id)
            for key in (
                "draft_id",
                "title",
                "created_at",
                "status",
                "band",
                "entry_count",
                "tags",
            ):
                self.assertIn(key, entry)
        times = [d["created_at"] for d in listing["drafts"]]
        self.assertEqual(times, sorted(times, reverse=True))

    def test_archive_query_filters(self):
        result = self._generate()
        status, listing = self._get_json("/api/archive?q=" + result["title"][:3])
        self.assertEqual(status, 200)
        self.assertTrue(any(d["draft_id"] == result["draft_id"] for d in listing["drafts"]))

    def test_tag_add_is_idempotent_and_logs_each_call(self):
        result = self._generate()
        draft_id = result["draft_id"]
        before = len(self._log_lines())
        status, entry = self._tag(
            {"draft_id": draft_id, "tag": "Launch", "action": "add"}
        )
        self.assertEqual(status, 200)
        self.assertIn("launch", entry["tags"])
        self.assertEqual(len(self._log_lines()), before + 1)
        status, again = self._tag(
            {"draft_id": draft_id, "tag": "launch", "action": "add"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(again["tags"], ["launch"])
        self.assertEqual(len(self._log_lines()), before + 2)
        seqs = [entry["seq"] for entry in again["audit"]]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))

    def test_tag_remove(self):
        result = self._generate()
        draft_id = result["draft_id"]
        self._tag({"draft_id": draft_id, "tag": "launch", "action": "add"})
        status, entry = self._tag(
            {"draft_id": draft_id, "tag": "launch", "action": "remove"}
        )
        self.assertEqual(status, 200)
        self.assertNotIn("launch", entry["tags"])

    def test_archive_entry_includes_audit(self):
        result = self._generate()
        draft_id = result["draft_id"]
        self._tag({"draft_id": draft_id, "tag": "launch", "action": "add"})
        status, entry = self._get_json("/api/archive/" + draft_id)
        self.assertEqual(status, 200)
        self.assertEqual(entry["draft_id"], draft_id)
        self.assertIn("launch", entry["tags"])
        self.assertEqual(len(entry["audit"]), 1)

    def test_archive_entry_unknown_is_404(self):
        status, body = self._get_json("/api/archive/does-not-exist")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_tag_unknown_draft_is_404_and_appends_nothing(self):
        before = len(self._log_lines())
        status, body = self._tag(
            {"draft_id": "missing", "tag": "launch", "action": "add"}
        )
        self.assertEqual(status, 404)
        self.assertIn("error", body)
        self.assertEqual(len(self._log_lines()), before)

    def test_tag_invalid_tag_is_400_and_appends_nothing(self):
        result = self._generate()
        before = len(self._log_lines())
        status, _body = self._tag(
            {"draft_id": result["draft_id"], "tag": "bad tag", "action": "add"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(len(self._log_lines()), before)

    def test_tag_invalid_action_is_400_and_appends_nothing(self):
        result = self._generate()
        before = len(self._log_lines())
        status, _body = self._tag(
            {"draft_id": result["draft_id"], "tag": "launch", "action": "flip"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(len(self._log_lines()), before)

    def test_tag_oversized_body_is_413(self):
        huge = "x" * (2 * 1024 * 1024 + 10)
        request = urllib.request.Request(
            self.base + "/api/archive/tag",
            data=json.dumps(
                {"draft_id": "d", "tag": "launch", "action": huge}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(ctx.exception.code, 413)

    def test_export_archive_csv(self):
        result = self._generate()
        self._tag({"draft_id": result["draft_id"], "tag": "launch", "action": "add"})
        status, headers, body = self._get_raw("/api/export/archive.csv")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        self.assertEqual(
            rows[0],
            ["draft_id", "title", "created_at", "status", "band", "entry_count", "tags"],
        )
        found = [row for row in rows[1:] if row[0] == result["draft_id"]]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][6], "launch")

    def test_export_archive_json(self):
        result = self._generate()
        status, headers, body = self._get_raw("/api/export/archive.json")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("attachment", headers["Content-Disposition"])
        exported = json.loads(body.decode("utf-8"))
        self.assertTrue(
            any(d["draft_id"] == result["draft_id"] for d in exported["drafts"])
        )

    def test_archive_sample(self):
        status, body = self._get_json("/api/archive/sample")
        self.assertEqual(status, 200)
        self.assertIn("drafts", body)
        self.assertTrue(body["drafts"])
        for entry in body["drafts"]:
            for key in ("draft_id", "title", "created_at", "status", "band", "tags"):
                self.assertIn(key, entry)


class ServerArchiveNoHistoryTests(_ReviewServerMixin, unittest.TestCase):
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

    def _get_status(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_archive_listing_without_history_is_404(self):
        status, body = self._get_status("/api/archive")
        self.assertEqual(status, 404)
        self.assertIn("DATA_DIR", json.loads(body.decode("utf-8"))["error"])

    def test_archive_entry_without_history_is_404(self):
        status, body = self._get_status("/api/archive/anything")
        self.assertEqual(status, 404)
        self.assertIn("DATA_DIR", json.loads(body.decode("utf-8"))["error"])

    def test_archive_export_without_history_is_404(self):
        status, body = self._get_status("/api/export/archive.csv")
        self.assertEqual(status, 404)
        self.assertIn("DATA_DIR", json.loads(body.decode("utf-8"))["error"])

    def test_tag_without_history_is_404(self):
        request = urllib.request.Request(
            self.base + "/api/archive/tag",
            data=json.dumps(
                {"draft_id": "d", "tag": "launch", "action": "add"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                status, body = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read()
        self.assertEqual(status, 404)
        self.assertIn("DATA_DIR", json.loads(body.decode("utf-8"))["error"])

    def test_archive_sample_needs_no_history(self):
        status, _body = self._get_status("/api/archive/sample")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
