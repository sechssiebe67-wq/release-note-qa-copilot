"""Offline tests for the release-note changelog digest.

Covers the pure ``compute_changelog`` helper (ordering, band aggregation, top
code tie-breaking and validation), the ``changelog_csv`` / ``changelog_json``
exporters (shape, quoting and stable keys) and the HTTP routes
(``POST /api/changelog``, ``GET /api/changelog/sample`` and both export routes)
including the 400/422/413 error paths.
"""

import csv
import io
import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from releasenote.changelog import compute_changelog
from releasenote.exporters import CHANGELOG_HEADER, changelog_csv, changelog_json
from releasenote.server import create_server
from releasenote.service import build_changelog, build_draft, sample_changelog

BAD = (
    "id,type,title,description,benefit,source\n"
    "REL-1,feature,Bulk export,Export all reports in one click,"
    "A much better experience,\n"
    "REL-2,fix,Timezone fix,Timestamps render in local time,"
    "No more confusing dates,\n"
    "REL-3,feature,Better search,Search is improved,Find things faster,\n"
)
REVIEW = (
    "id,type,title,description,benefit,source\n"
    "REL-1,feature,Bulk export,Export all reports in one click,"
    "A much better experience,\n"
    "REL-2,fix,Timezone fix,Timestamps render in local time,"
    "Correct local times,REL-2\n"
)
CLEAN = (
    "id,type,title,description,benefit,source\n"
    "REL-1,feature,Bulk export,Export all reports in one click,"
    "One-click export saves time,REL-1\n"
    "REL-2,fix,Timezone fix,Timestamps render in local time,"
    "Correct local times,REL-2\n"
)
UNPARSEABLE = "foo,bar\n1,2\n"


def _release(label, content, date=None):
    item = {"label": label, "content": content, "format": "csv"}
    if date is not None:
        item["date"] = date
    return item


def _draft(label, content, date=None):
    """Build a real draft payload and attach the digest label/date."""
    draft = build_draft(content, "csv", label)
    draft["label"] = label
    if date is not None:
        draft["date"] = date
    return draft


def _flag(code, severity="low"):
    return {"code": code, "severity": severity, "message": "", "entry_id": ""}


def _payload(label, title, band, counts, flags=None):
    """Hand-build a minimal draft payload with an explicit band/counts."""
    return {
        "label": label,
        "title": title,
        "score": {"band": band},
        "qa": {
            "publishable": counts[0] == 0,
            "counts": {
                "high": counts[0],
                "medium": counts[1],
                "low": counts[2],
                "total": counts[0] + counts[1] + counts[2],
            },
            "flags": flags or [],
        },
        "entry_count": 1,
    }


class ComputeChangelogTests(unittest.TestCase):
    def test_shape_and_order(self):
        report = compute_changelog(
            [
                _draft("2026.1", BAD, "2026-01-15"),
                _draft("2026.2", REVIEW, "2026-02-15"),
                _draft("2026.3", CLEAN, "2026-03-15"),
            ]
        )
        self.assertEqual(
            set(report),
            {
                "releases",
                "total_releases",
                "ready_count",
                "review_count",
                "risky_count",
                "overall_verdict",
            },
        )
        self.assertEqual(
            [row["label"] for row in report["releases"]],
            ["2026.1", "2026.2", "2026.3"],
        )
        for row in report["releases"]:
            self.assertEqual(
                set(row),
                {
                    "label",
                    "date",
                    "title",
                    "band",
                    "publishable",
                    "counts",
                    "top_codes",
                    "entry_count",
                    "summary",
                },
            )
            self.assertTrue(row["summary"])
            self.assertLessEqual(len(row["top_codes"]), 3)

    def test_dates_are_optional(self):
        report = compute_changelog(
            [_draft("a", CLEAN), _draft("b", CLEAN, "2026-02-15")]
        )
        self.assertEqual(report["releases"][0]["date"], "")
        self.assertEqual(report["releases"][1]["date"], "2026-02-15")

    def test_counts_match_single_draft(self):
        report = compute_changelog([_draft("a", BAD), _draft("b", CLEAN)])
        draft = build_draft(BAD, "csv")
        self.assertEqual(report["releases"][0]["counts"], draft["qa"]["counts"])
        self.assertEqual(
            report["releases"][0]["publishable"], draft["qa"]["publishable"]
        )
        self.assertEqual(report["releases"][0]["entry_count"], draft["entry_count"])

    def test_band_aggregation(self):
        report = compute_changelog(
            [
                _payload("a", "a", "ready", (0, 0, 0)),
                _payload("b", "b", "review", (0, 1, 0)),
                _payload("c", "c", "risky", (2, 0, 0)),
            ]
        )
        self.assertEqual(report["ready_count"], 1)
        self.assertEqual(report["review_count"], 1)
        self.assertEqual(report["risky_count"], 1)
        self.assertEqual(report["total_releases"], 3)
        self.assertEqual(report["overall_verdict"], "risky")
        self.assertEqual(report["releases"][0]["band"], "ready")
        self.assertEqual(report["releases"][2]["band"], "risky")

    def test_overall_verdict_all_ready(self):
        report = compute_changelog(
            [_payload("a", "a", "ready", (0, 0, 0)),
             _payload("b", "b", "ready", (0, 0, 0))]
        )
        self.assertEqual(report["overall_verdict"], "ready")

    def test_overall_verdict_review_otherwise(self):
        report = compute_changelog(
            [_payload("a", "a", "ready", (0, 0, 0)),
             _payload("b", "b", "review", (0, 1, 0))]
        )
        self.assertEqual(report["overall_verdict"], "review")

    def test_overall_verdict_any_risky(self):
        report = compute_changelog(
            [_payload("a", "a", "ready", (0, 0, 0)),
             _payload("b", "b", "risky", (2, 0, 0)),
             _payload("c", "c", "review", (0, 1, 0))]
        )
        self.assertEqual(report["overall_verdict"], "risky")

    def test_top_codes_ordered_by_frequency_then_code(self):
        flags = [
            _flag("beta"),
            _flag("beta"),
            _flag("alpha"),
            _flag("alpha"),
            _flag("gamma"),
            _flag("delta"),
        ]
        report = compute_changelog(
            [_payload("a", "a", "review", (0, 0, 0), flags),
             _payload("b", "b", "ready", (0, 0, 0))]
        )
        self.assertEqual(
            report["releases"][0]["top_codes"], ["alpha", "beta", "delta"]
        )

    def test_top_codes_capped_at_three(self):
        flags = [_flag(code) for code in ("a", "b", "c", "d", "e")]
        report = compute_changelog(
            [_payload("a", "a", "review", (0, 0, 0), flags),
             _payload("b", "b", "ready", (0, 0, 0))]
        )
        self.assertEqual(len(report["releases"][0]["top_codes"]), 3)

    def test_top_codes_empty_without_flags(self):
        report = compute_changelog(
            [_draft("a", CLEAN), _draft("b", CLEAN)]
        )
        self.assertEqual(report["releases"][0]["top_codes"], [])

    def test_publishable_falls_back_to_no_high_flags(self):
        payload = {
            "label": "a",
            "title": "a",
            "score": {"band": "ready"},
            "qa": {"counts": {"high": 1, "medium": 0, "low": 0, "total": 1}},
            "entry_count": 1,
        }
        report = compute_changelog([payload, _draft("b", CLEAN)])
        self.assertFalse(report["releases"][0]["publishable"])

    def test_validation_rejects_non_list(self):
        with self.assertRaises(ValueError):
            compute_changelog({"releases": []})

    def test_validation_rejects_non_object_item(self):
        with self.assertRaises(ValueError):
            compute_changelog([_draft("a", CLEAN), "not-an-object"])

    def test_validation_rejects_missing_label(self):
        draft = _draft("a", CLEAN)
        del draft["label"]
        with self.assertRaises(ValueError):
            compute_changelog([draft, _draft("b", CLEAN)])

    def test_validation_rejects_missing_title(self):
        draft = _draft("a", CLEAN)
        del draft["title"]
        with self.assertRaises(ValueError):
            compute_changelog([draft, _draft("b", CLEAN)])

    def test_validation_rejects_too_few(self):
        for count in (0, 1):
            drafts = [_draft("r{}".format(i), CLEAN) for i in range(count)]
            with self.assertRaises(ValueError):
                compute_changelog(drafts)

    def test_validation_rejects_more_than_50(self):
        drafts = [_draft("r{}".format(i), CLEAN) for i in range(51)]
        with self.assertRaises(ValueError):
            compute_changelog(drafts)

    def test_validation_accepts_fifty(self):
        drafts = [_draft("r{}".format(i), CLEAN) for i in range(50)]
        report = compute_changelog(drafts)
        self.assertEqual(report["total_releases"], 50)


class BuildChangelogTests(unittest.TestCase):
    def test_builds_digest_from_releases(self):
        report = build_changelog(
            [
                _release("2026.1", BAD, "2026-01-15"),
                _release("2026.2", CLEAN, "2026-02-15"),
            ]
        )
        self.assertEqual(report["total_releases"], 2)
        self.assertEqual(report["releases"][0]["label"], "2026.1")
        self.assertEqual(report["releases"][0]["title"], "2026.1")
        self.assertEqual(report["releases"][0]["date"], "2026-01-15")
        self.assertEqual(report["releases"][0]["band"], "risky")
        self.assertEqual(report["overall_verdict"], "risky")

    def test_profile_respected(self):
        report = build_changelog(
            [_release("a", BAD), _release("b", CLEAN)], profile="strict"
        )
        draft = build_draft(BAD, "csv", "a", None, "strict")
        self.assertEqual(
            report["releases"][0]["counts"], draft["qa"]["counts"]
        )

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            build_changelog("nope")
        with self.assertRaises(ValueError):
            build_changelog([_release("a", CLEAN)])
        with self.assertRaises(ValueError):
            build_changelog([{"label": "a"}, _release("b", CLEAN)])
        with self.assertRaises(ValueError):
            build_changelog([{"content": CLEAN}, _release("b", CLEAN)])

    def test_sample_is_valid(self):
        report = build_changelog(sample_changelog()["releases"])
        self.assertEqual(report["total_releases"], 3)
        bands = {row["band"] for row in report["releases"]}
        self.assertEqual(bands, {"ready", "review", "risky"})


class ChangelogExportTests(unittest.TestCase):
    def _report(self):
        return build_changelog(
            [
                _release("2026.1", BAD, "2026-01-15"),
                _release("2026.2", CLEAN),
            ]
        )

    def test_csv_header_and_rows(self):
        rows = list(csv.reader(io.StringIO(changelog_csv(self._report()))))
        self.assertEqual(rows[0], list(CHANGELOG_HEADER))
        self.assertEqual(
            rows[0],
            [
                "label",
                "date",
                "title",
                "band",
                "publishable",
                "high",
                "medium",
                "low",
                "total",
                "entry_count",
                "top_codes",
                "summary",
            ],
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][0], "2026.1")
        self.assertEqual(rows[1][3], "risky")
        self.assertEqual(rows[1][4], "False")

    def test_csv_top_codes_joined_with_pipe(self):
        rows = list(csv.reader(io.StringIO(changelog_csv(self._report()))))
        self.assertIn("|", rows[1][10])

    def test_csv_uses_lf_line_endings(self):
        text = changelog_csv(self._report())
        self.assertNotIn("\r", text)
        self.assertTrue(text.endswith("\n"))

    def test_csv_quotes_commas_quotes_and_newlines(self):
        payload = {
            "releases": [
                {
                    "label": 'café, "q"',
                    "date": "",
                    "title": "Title, with comma",
                    "band": "review",
                    "publishable": False,
                    "counts": {"high": 0, "medium": 1, "low": 0, "total": 1},
                    "top_codes": ["a", "b"],
                    "entry_count": 1,
                    "summary": 'line one, "quoted"\nline two',
                },
                {
                    "label": "plain",
                    "date": "",
                    "title": "plain",
                    "band": "ready",
                    "publishable": True,
                    "counts": {"high": 0, "medium": 0, "low": 0, "total": 0},
                    "top_codes": [],
                    "entry_count": 1,
                    "summary": "clean",
                },
            ]
        }
        rows = list(csv.reader(io.StringIO(changelog_csv(payload))))
        self.assertEqual(rows[1][0], 'café, "q"')
        self.assertEqual(rows[1][2], "Title, with comma")
        self.assertEqual(rows[1][11], 'line one, "quoted"\nline two')

    def test_csv_is_stable(self):
        report = self._report()
        self.assertEqual(changelog_csv(report), changelog_csv(report))

    def test_csv_without_releases_is_header_only(self):
        self.assertEqual(changelog_csv({}), ",".join(CHANGELOG_HEADER) + "\n")

    def test_json_is_stable_and_nan_free(self):
        report = self._report()
        text = changelog_json(report)
        self.assertEqual(text, changelog_json(report))
        self.assertNotIn("NaN", text)
        self.assertEqual(
            text,
            json.dumps(
                report, ensure_ascii=False, sort_keys=True, allow_nan=False
            ),
        )
        self.assertEqual(len(json.loads(text)["releases"]), 2)


class _ChangelogServerMixin:
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

    def _get_export(self, path, params):
        query = urllib.parse.urlencode(params)
        return self._get(path + "?" + query)


class ChangelogServerTests(_ChangelogServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._start()

    @classmethod
    def tearDownClass(cls):
        cls._stop()

    def test_happy_path(self):
        status, report = self._post(
            "/api/changelog",
            {"releases": [_release("2026.1", BAD), _release("2026.2", CLEAN)]},
        )
        self.assertEqual(status, 200)
        for key in (
            "releases",
            "total_releases",
            "ready_count",
            "review_count",
            "risky_count",
            "overall_verdict",
        ):
            self.assertIn(key, report)
        row = report["releases"][0]
        for key in (
            "label",
            "date",
            "title",
            "band",
            "publishable",
            "counts",
            "top_codes",
            "entry_count",
            "summary",
        ):
            self.assertIn(key, row)
        self.assertIsInstance(row["counts"]["high"], int)
        self.assertIsInstance(row["counts"]["medium"], int)
        self.assertIsInstance(row["counts"]["low"], int)
        self.assertIsInstance(row["counts"]["total"], int)
        self.assertTrue(row["summary"])

    def test_band_and_overall_verdict(self):
        status, report = self._post(
            "/api/changelog",
            {
                "releases": [
                    _release("a", CLEAN),
                    _release("b", CLEAN),
                ]
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(report["overall_verdict"], "ready")
        self.assertEqual(report["ready_count"], 2)

    def test_bounds_are_400(self):
        for count in (0, 1, 51):
            releases = [
                _release("r{}".format(i), CLEAN) for i in range(count)
            ]
            status, body = self._post("/api/changelog", {"releases": releases})
            self.assertEqual(status, 400, count)
            self.assertIn("error", body)

    def test_non_list_releases_is_400(self):
        status, body = self._post("/api/changelog", {"releases": "nope"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_missing_content_is_400(self):
        status, body = self._post(
            "/api/changelog",
            {"releases": [{"label": "a"}, _release("b", CLEAN)]},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_missing_label_is_400(self):
        status, body = self._post(
            "/api/changelog",
            {"releases": [{"content": CLEAN}, _release("b", CLEAN)]},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_unknown_profile_is_400(self):
        status, body = self._post(
            "/api/changelog",
            {
                "releases": [_release("a", CLEAN), _release("b", CLEAN)],
                "profile": "nope",
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("nope", body["error"])

    def test_unparseable_is_422(self):
        status, body = self._post(
            "/api/changelog",
            {"releases": [_release("a", UNPARSEABLE), _release("b", CLEAN)]},
        )
        self.assertEqual(status, 422)
        self.assertIn("error", body)

    def test_oversized_body_is_413(self):
        huge = "x" * (2 * 1024 * 1024 + 10)
        status, _body = self._post(
            "/api/changelog",
            {"releases": [_release("a", huge), _release("b", CLEAN)]},
        )
        self.assertEqual(status, 413)

    def test_sample_endpoint_is_valid(self):
        status, _headers, body = self._get("/api/changelog/sample")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(len(payload["releases"]), 3)
        status, report = self._post("/api/changelog", payload)
        self.assertEqual(status, 200)
        self.assertEqual(len(report["releases"]), 3)

    def _export_params(self):
        return [
            ("format", "csv"),
            ("release", "2026.1:" + BAD),
            ("release", "2026.2:" + CLEAN),
        ]

    def test_csv_export(self):
        status, headers, body = self._get_export(
            "/api/export/changelog.csv", self._export_params()
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertNotIn(b"\r", body)
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        self.assertEqual(rows[0], list(CHANGELOG_HEADER))
        self.assertEqual(len(rows), 3)

    def test_json_export(self):
        status, headers, body = self._get_export(
            "/api/export/changelog.json", self._export_params()
        )
        self.assertEqual(status, 200)
        self.assertTrue(
            headers["Content-Type"].startswith("application/json")
        )
        self.assertIn("attachment", headers["Content-Disposition"])
        report = json.loads(body.decode("utf-8"))
        self.assertEqual(len(report["releases"]), 2)
        self.assertIn("overall_verdict", report)

    def test_export_requires_two_releases(self):
        status, _headers, body = self._get_export(
            "/api/export/changelog.csv", [("release", "only:" + CLEAN)]
        )
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)

    def test_export_bad_release_param_is_400(self):
        params = [("release", "no-colon-here"), ("release", "r2:" + CLEAN)]
        status, _headers, body = self._get_export(
            "/api/export/changelog.csv", params
        )
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)

    def test_export_unknown_profile_is_400(self):
        params = self._export_params() + [("profile", "nope")]
        status, _headers, body = self._get_export(
            "/api/export/changelog.csv", params
        )
        self.assertEqual(status, 400)
        self.assertIn(b"nope", body)


if __name__ == "__main__":
    unittest.main()
