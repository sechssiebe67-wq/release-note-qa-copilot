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

from releasenote.compare import (
    MAX_DRAFTS,
    MIN_DRAFTS,
    _entry_key,
    _recommend,
    compute_comparison,
)
from releasenote.config import WorkspaceConfig
from releasenote.exporters import (
    COMPARISON_HEADER,
    COMPARISON_MATRIX_HEADER,
    comparison_csv,
    comparison_json,
    comparison_matrix_csv,
)
from releasenote.importer import ParseError
from releasenote.models import ChangeEntry
from releasenote.profiles import UnknownProfileError
from releasenote.server import create_server
from releasenote.service import build_comparison, build_draft, sample_comparison

# Two drafts that share REL-1 unchanged, differ on REL-2 (draft b changes its
# benefit) and each carry one draft-only entry.
DRAFT_A = (
    "id,type,title,description,benefit,source\n"
    "REL-1,feature,Bulk export,Export all reports,Saves time,REL-1\n"
    "REL-2,fix,Timezone fix,Timestamps render locally,Correct local times,REL-2\n"
    "REL-5,fix,Retry logic,Retries transient failures,Stable uploads,REL-5\n"
)
DRAFT_B = (
    "id,type,title,description,benefit,source\n"
    "REL-1,feature,Bulk export,Export all reports,Saves time,REL-1\n"
    "REL-2,fix,Timezone fix,Timestamps render locally,Local time everywhere,REL-2\n"
    "REL-3,feature,Search filters,Filter results by team,Relevant results only,REL-3\n"
)
IDENTICAL = (
    "id,type,title,description,benefit,source\n"
    "REL-1,feature,Bulk export,Export all reports,Saves time,REL-1\n"
    "REL-2,fix,Timezone fix,Timestamps render locally,Correct local times,REL-2\n"
)
UNPARSEABLE = "foo,bar\n1,2\n"


def _draft(label, content, fmt="csv"):
    return {"label": label, "content": content, "format": fmt}


def _row(label, high=0, medium=0, low=0, sourced=1.0):
    return {
        "label": label,
        "counts": {"high": high, "medium": medium, "low": low,
                   "total": high + medium + low},
        "sourced_ratio": sourced,
    }


class ComputeComparisonTests(unittest.TestCase):
    def test_shape(self):
        report = compute_comparison([_draft("a", DRAFT_A), _draft("b", DRAFT_B)])
        self.assertEqual(
            set(report),
            {"drafts", "matrix", "shared", "unique", "recommendation"},
        )
        for row in report["drafts"]:
            self.assertEqual(
                set(row),
                {
                    "label",
                    "entry_count",
                    "counts",
                    "publishable",
                    "sourced_ratio",
                    "vague_hits",
                    "banned_hits",
                },
            )

    def test_metrics_match_single_draft(self):
        report = compute_comparison([_draft("a", DRAFT_A), _draft("b", DRAFT_B)])
        draft = build_draft(DRAFT_A, "csv")
        self.assertEqual(report["drafts"][0]["counts"], draft["qa"]["counts"])
        self.assertEqual(report["drafts"][0]["entry_count"], draft["entry_count"])
        self.assertEqual(
            report["drafts"][0]["publishable"], draft["qa"]["publishable"]
        )

    def test_identical_drafts_share_every_entry(self):
        report = compute_comparison(
            [_draft("a", IDENTICAL), _draft("b", IDENTICAL)]
        )
        self.assertEqual(report["shared"], ["rel-1", "rel-2"])
        self.assertEqual(report["unique"], {"a": [], "b": []})
        for row in report["matrix"]:
            self.assertEqual(
                row["presence"], {"a": "identical", "b": "identical"}
            )

    def test_changed_entry_marked_changed(self):
        report = compute_comparison([_draft("a", DRAFT_A), _draft("b", DRAFT_B)])
        matrix = {row["key"]: row for row in report["matrix"]}
        self.assertEqual(
            matrix["rel-2"]["presence"], {"a": "identical", "b": "changed"}
        )

    def test_shared_entry_present_in_every_draft(self):
        report = compute_comparison([_draft("a", DRAFT_A), _draft("b", DRAFT_B)])
        # Shared means present in every draft, whether identical or changed.
        self.assertEqual(report["shared"], ["rel-1", "rel-2"])

    def test_draft_only_entry_lands_in_unique(self):
        report = compute_comparison([_draft("a", DRAFT_A), _draft("b", DRAFT_B)])
        self.assertEqual(report["unique"], {"a": ["rel-5"], "b": ["rel-3"]})
        matrix = {row["key"]: row for row in report["matrix"]}
        self.assertEqual(matrix["rel-3"]["presence"], {"b": "unique"})
        self.assertEqual(matrix["rel-5"]["presence"], {"a": "unique"})

    def test_entry_key_falls_back_to_normalized_title(self):
        entry = ChangeEntry(title="Bulk  Export", id="")
        self.assertEqual(_entry_key(entry), "bulk export")
        self.assertEqual(_entry_key(ChangeEntry(title="X", id="REL-9")), "rel-9")

    def test_vague_and_banned_hits(self):
        vague = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Sync,Sync is guaranteed,Guaranteed results,A\n"
        )
        report = compute_comparison(
            [_draft("a", vague), _draft("b", IDENTICAL)],
            config={"banned_phrases": ["guaranteed"]},
        )
        self.assertEqual(report["drafts"][0]["banned_hits"], 1)
        self.assertEqual(report["drafts"][0]["vague_hits"], 0)


class RecommendationTests(unittest.TestCase):
    def test_fewest_high_wins(self):
        best = _recommend([_row("a", high=1, medium=0), _row("b", high=0, medium=5)])
        self.assertEqual(best["label"], "b")

    def test_total_breaks_high_tie(self):
        best = _recommend([_row("a", medium=3), _row("b", medium=1)])
        self.assertEqual(best["label"], "b")

    def test_sourced_ratio_breaks_high_and_total_tie(self):
        best = _recommend(
            [_row("a", medium=1, sourced=0.5), _row("b", medium=1, sourced=1.0)]
        )
        self.assertEqual(best["label"], "b")

    def test_input_order_breaks_full_tie(self):
        best = _recommend([_row("a", medium=1), _row("b", medium=1)])
        self.assertEqual(best["label"], "a")

    def test_recommendation_in_report_prefers_clean_draft(self):
        bad = (
            "id,type,title,description,benefit,source\n"
            "REL-1,feature,Bulk export,Export all reports,A better experience,\n"
        )
        report = compute_comparison([_draft("bad", bad), _draft("good", IDENTICAL)])
        self.assertEqual(report["recommendation"]["label"], "good")
        self.assertIn("reason", report["recommendation"])


class ValidationTests(unittest.TestCase):
    def test_rejects_one_and_eleven_drafts(self):
        for count in (0, 1, MAX_DRAFTS + 1):
            drafts = [_draft("d{}".format(i), IDENTICAL) for i in range(count)]
            with self.assertRaises(ValueError):
                compute_comparison(drafts)

    def test_accepts_two_and_ten_drafts(self):
        for count in (MIN_DRAFTS, MAX_DRAFTS):
            drafts = [_draft("d{}".format(i), IDENTICAL) for i in range(count)]
            report = compute_comparison(drafts)
            self.assertEqual(len(report["drafts"]), count)

    def test_rejects_non_list(self):
        with self.assertRaises(ValueError):
            compute_comparison("nope")

    def test_rejects_non_mapping_draft(self):
        with self.assertRaises(ValueError):
            compute_comparison([_draft("a", IDENTICAL), "nope"])

    def test_rejects_missing_or_blank_content(self):
        with self.assertRaises(ValueError):
            compute_comparison([_draft("a", "   "), _draft("b", IDENTICAL)])
        with self.assertRaises(ValueError):
            compute_comparison([{"label": "a"}, _draft("b", IDENTICAL)])

    def test_missing_label_gets_default(self):
        report = compute_comparison(
            [{"content": IDENTICAL}, {"content": IDENTICAL}]
        )
        self.assertEqual(
            [row["label"] for row in report["drafts"]],
            ["Draft 1", "Draft 2"],
        )

    def test_unparseable_raises_parse_error(self):
        with self.assertRaises(ParseError):
            compute_comparison(
                [_draft("a", UNPARSEABLE), _draft("b", IDENTICAL)]
            )

    def test_unknown_profile_raises(self):
        with self.assertRaises(UnknownProfileError):
            compute_comparison(
                [_draft("a", IDENTICAL), _draft("b", IDENTICAL)],
                config={"profile": "nope"},
            )

    def test_inline_config_reranks_counts(self):
        vague = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Better sync,The sync is improved,"
            "A much better experience,A\n"
        )
        default = compute_comparison([_draft("a", vague), _draft("b", vague)])
        self.assertEqual(default["drafts"][0]["counts"]["high"], 0)
        overridden = compute_comparison(
            [_draft("a", vague), _draft("b", vague)],
            config={"severity_overrides": {"vague_language": "high"}},
        )
        self.assertEqual(overridden["drafts"][0]["counts"]["high"], 1)


class BuildComparisonTests(unittest.TestCase):
    def test_adds_config_and_profile(self):
        report = build_comparison(
            {"drafts": [ _draft("a", IDENTICAL), _draft("b", IDENTICAL)],
             "profile": "strict"}
        )
        self.assertEqual(report["profile"], "strict")
        self.assertEqual(
            report["config"]["severity_overrides"].get("vague_language"), "high"
        )

    def test_default_config_echoed(self):
        report = build_comparison(
            {"drafts": [_draft("a", IDENTICAL), _draft("b", IDENTICAL)]}
        )
        self.assertIsNone(report["profile"])
        self.assertEqual(report["config"], WorkspaceConfig.default().to_dict())

    def test_rejects_bad_payload(self):
        with self.assertRaises(ValueError):
            build_comparison([_draft("a", IDENTICAL)])

    def test_rejects_one_draft(self):
        with self.assertRaises(ValueError):
            build_comparison({"drafts": [_draft("a", IDENTICAL)]})

    def test_unknown_profile_raises(self):
        with self.assertRaises(UnknownProfileError):
            build_comparison(
                {"drafts": [_draft("a", IDENTICAL), _draft("b", IDENTICAL)],
                 "profile": "nope"}
            )

    def test_sample_is_valid(self):
        report = build_comparison(sample_comparison())
        self.assertEqual(len(report["drafts"]), 2)
        self.assertEqual(set(report["unique"]), {"branch-a", "branch-b"})
        self.assertTrue(report["shared"])
        self.assertIn("recommendation", report)


class ComparisonExportTests(unittest.TestCase):
    def _report(self):
        return compute_comparison([_draft("a", DRAFT_A), _draft("b", DRAFT_B)])

    def test_csv_header_and_rows(self):
        rows = list(csv.reader(io.StringIO(comparison_csv(self._report()))))
        self.assertEqual(
            rows[0],
            [
                "label",
                "entry_count",
                "high",
                "medium",
                "low",
                "total",
                "publishable",
                "sourced_ratio",
                "vague_hits",
                "banned_hits",
            ],
        )
        self.assertEqual(list(COMPARISON_HEADER), rows[0])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][0], "a")

    def test_csv_without_drafts_is_header_only(self):
        self.assertEqual(
            comparison_csv({}), ",".join(COMPARISON_HEADER) + "\n"
        )

    def test_matrix_csv_header_and_cells(self):
        rows = list(csv.reader(io.StringIO(comparison_matrix_csv(self._report()))))
        self.assertEqual(rows[0][:4], list(COMPARISON_MATRIX_HEADER))
        self.assertEqual(rows[0][4:], ["a", "b"])
        by_key = {row[0]: row for row in rows[1:]}
        self.assertEqual(by_key["rel-2"][4:], ["identical", "changed"])
        self.assertEqual(by_key["rel-3"][4:], ["", "unique"])

    def test_matrix_csv_quotes_labels_with_commas(self):
        report = {
            "drafts": [
                {"label": 'café, "q"', "entry_count": 1, "counts": {},
                 "publishable": True, "sourced_ratio": 1.0,
                 "vague_hits": 0, "banned_hits": 0},
                {"label": "b", "entry_count": 1, "counts": {},
                 "publishable": True, "sourced_ratio": 1.0,
                 "vague_hits": 0, "banned_hits": 0},
            ],
            "matrix": [
                {"key": "k", "id": "K", "title": 'Title, "x"', "type": "fix",
                 "presence": {'café, "q"': "identical", "b": "unique"}},
            ],
        }
        rows = list(csv.reader(io.StringIO(comparison_matrix_csv(report))))
        self.assertEqual(rows[0], ["key", "id", "title", "type", 'café, "q"', "b"])
        self.assertEqual(rows[1], ["k", "K", 'Title, "x"', "fix", "identical", "unique"])

    def test_json_is_stable_and_nan_free(self):
        report = self._report()
        text = comparison_json(report)
        self.assertEqual(text, comparison_json(report))
        self.assertNotIn("NaN", text)
        self.assertEqual(
            text,
            json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False),
        )


class _CompareServerMixin:
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

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()


class CompareServerTests(_CompareServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._start()

    @classmethod
    def tearDownClass(cls):
        cls._stop()

    def test_compare_happy_path(self):
        status, report = self._post(
            "/api/compare",
            {"drafts": [_draft("a", DRAFT_A), _draft("b", DRAFT_B)]},
        )
        self.assertEqual(status, 200)
        for key in (
            "drafts",
            "matrix",
            "shared",
            "unique",
            "recommendation",
            "config",
            "profile",
        ):
            self.assertIn(key, report)

    def test_compare_matches_trend_metrics(self):
        status, report = self._post(
            "/api/compare",
            {"drafts": [_draft("a", DRAFT_A), _draft("b", DRAFT_B)]},
        )
        self.assertEqual(status, 200)
        trend_status, trend = self._post(
            "/api/trend",
            {"releases": [_draft("a", DRAFT_A), _draft("b", DRAFT_B)]},
        )
        self.assertEqual(trend_status, 200)
        self.assertEqual(report["drafts"][0], trend["releases"][0])
        self.assertEqual(report["drafts"][1], trend["releases"][1])

    def test_compare_bounds_are_400(self):
        for count in (1, MAX_DRAFTS + 1):
            drafts = [_draft("d{}".format(i), IDENTICAL) for i in range(count)]
            status, body = self._post("/api/compare", {"drafts": drafts})
            self.assertEqual(status, 400, count)
            self.assertIn("error", body)

    def test_compare_missing_content_is_400(self):
        status, body = self._post(
            "/api/compare",
            {"drafts": [{"label": "a"}, _draft("b", IDENTICAL)]},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_compare_non_object_body_is_400(self):
        request = urllib.request.Request(
            self.base + "/api/compare",
            data=b"[1, 2, 3]",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 400)

    def test_compare_invalid_config_is_400(self):
        status, body = self._post(
            "/api/compare",
            {"drafts": [_draft("a", IDENTICAL), _draft("b", IDENTICAL)],
             "config": {"nope": []}},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_compare_unknown_profile_is_400(self):
        status, body = self._post(
            "/api/compare",
            {"drafts": [_draft("a", IDENTICAL), _draft("b", IDENTICAL)],
             "profile": "nope"},
        )
        self.assertEqual(status, 400)
        self.assertIn("nope", body["error"])

    def test_compare_unparseable_is_422(self):
        status, body = self._post(
            "/api/compare",
            {"drafts": [_draft("a", UNPARSEABLE), _draft("b", IDENTICAL)]},
        )
        self.assertEqual(status, 422)
        self.assertIn("error", body)

    def test_sample_endpoint_is_valid(self):
        status, _headers, body = self._get("/api/compare/sample")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        status, report = self._post("/api/compare", payload)
        self.assertEqual(status, 200)
        self.assertEqual(len(report["drafts"]), 2)

    def test_comparison_csv_export(self):
        params = [
            ("format", "csv"),
            ("draft", "a:" + DRAFT_A),
            ("draft", "b:" + DRAFT_B),
        ]
        query = urllib.parse.urlencode(params)
        status, headers, body = self._get(
            "/api/export/comparison.csv?" + query
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        self.assertEqual(rows[0], list(COMPARISON_HEADER))

    def test_comparison_matrix_csv_export(self):
        params = [
            ("format", "csv"),
            ("draft", "a:" + DRAFT_A),
            ("draft", "b:" + DRAFT_B),
        ]
        query = urllib.parse.urlencode(params)
        status, headers, body = self._get(
            "/api/export/comparison-matrix.csv?" + query
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        self.assertEqual(rows[0][4:], ["a", "b"])

    def test_comparison_json_export(self):
        params = [
            ("format", "csv"),
            ("draft", "a:" + DRAFT_A),
            ("draft", "b:" + DRAFT_B),
        ]
        query = urllib.parse.urlencode(params)
        status, headers, body = self._get(
            "/api/export/comparison.json?" + query
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertNotIn("NaN", body.decode("utf-8"))
        report = json.loads(body.decode("utf-8"))
        self.assertEqual(len(report["drafts"]), 2)

    def test_comparison_export_requires_two_drafts(self):
        query = urllib.parse.urlencode([("draft", "only:" + IDENTICAL)])
        status, _headers, body = self._get(
            "/api/export/comparison.csv?" + query
        )
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)

    def test_comparison_export_bad_param_is_400(self):
        params = [("draft", "no-colon"), ("draft", "b:" + IDENTICAL)]
        query = urllib.parse.urlencode(params)
        status, _headers, body = self._get(
            "/api/export/comparison.csv?" + query
        )
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)

    def test_comparison_export_unknown_profile_is_400(self):
        params = [
            ("draft", "a:" + IDENTICAL),
            ("draft", "b:" + IDENTICAL),
            ("profile", "nope"),
        ]
        query = urllib.parse.urlencode(params)
        status, _headers, body = self._get(
            "/api/export/comparison.csv?" + query
        )
        self.assertEqual(status, 400)
        self.assertIn(b"nope", body)


class CompareHistoryTests(_CompareServerMixin, unittest.TestCase):
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

    def _history(self):
        _status, _headers, body = self._get("/api/history")
        return json.loads(body.decode("utf-8"))["drafts"]

    def test_failed_compare_does_not_store(self):
        before = len(self._history())
        for payload in (
            {"drafts": [_draft("a", IDENTICAL)]},
            {"drafts": [_draft("a", UNPARSEABLE), _draft("b", IDENTICAL)]},
            {"drafts": [_draft("a", IDENTICAL), _draft("b", IDENTICAL)],
             "profile": "nope"},
        ):
            self._post("/api/compare", payload)
        self.assertEqual(len(self._history()), before)

    def test_successful_compare_does_not_store(self):
        before = len(self._history())
        status, _report = self._post(
            "/api/compare",
            {"drafts": [_draft("a", IDENTICAL), _draft("b", IDENTICAL)]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(self._history()), before)


if __name__ == "__main__":
    unittest.main()
