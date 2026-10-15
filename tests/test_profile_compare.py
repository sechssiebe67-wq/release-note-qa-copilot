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

from releasenote.exporters import (
    PROFILE_COMPARISON_HEADER,
    profile_comparison_csv,
    profile_comparison_json,
)
from releasenote.importer import ParseError
from releasenote.profile_compare import compare_profiles
from releasenote.profiles import BUILTIN_PROFILES, UnknownProfileError
from releasenote.server import create_server
from releasenote.service import build_profile_comparison, sample_profile_comparison

# A draft whose only feature benefits from a banned hype phrase and a vague
# word: strict reranks the vague term, marketing-safe bans the phrase and
# internal leaves it alone, so all three profiles score it differently.
HYPE = (
    "id,type,title,description,benefit,source\n"
    "REL-1,feature,Bulk export,Export all reports in one click,"
    "The world's best export experience,REL-1\n"
)

# A draft that is clean under every profile (a Breaking Changes section is
# present so the strict profile is satisfied too).
CLEAN = (
    "id,type,title,description,benefit,source\n"
    "REL-1,breaking,Remove legacy API,The legacy endpoint is retired,"
    "Migrate to the new endpoint,REL-1\n"
    "REL-2,fix,Timezone fix,Timestamps render in local time,"
    "Correct local times,REL-2\n"
)

VAGUE = (
    "id,type,title,description,benefit,source\n"
    "A,fix,Better sync,The sync is improved,A much better experience,A\n"
)

UNPARSEABLE = "foo,bar\n1,2\n"


class CompareProfilesTests(unittest.TestCase):
    def _by_name(self, report):
        return {row["name"]: row for row in report["profiles"]}

    def test_all_builtins_compared_in_stable_order(self):
        report = compare_profiles(VAGUE, "csv")
        self.assertEqual(
            [row["name"] for row in report["profiles"]],
            list(BUILTIN_PROFILES),
        )
        self.assertEqual(report["baseline"], list(BUILTIN_PROFILES)[0])
        for row in report["profiles"]:
            self.assertEqual(
                set(row),
                {
                    "name",
                    "description",
                    "entry_count",
                    "counts",
                    "publishable",
                    "sourced_ratio",
                    "vague_hits",
                    "banned_hits",
                    "flag_counts",
                    "flag_severities",
                    "delta",
                },
            )

    def test_rows_match_single_draft_metrics(self):
        from releasenote.importer import parse_entries
        from releasenote.profiles import resolve_config
        from releasenote.qa import analyze_entries

        report = compare_profiles(VAGUE, "csv", profiles=["strict"])
        row = report["profiles"][0]
        entries = parse_entries(VAGUE, "csv")
        qa = analyze_entries(entries, resolve_config({"profile": "strict"}))
        self.assertEqual(row["counts"], qa.to_dict()["counts"])
        self.assertEqual(row["publishable"], qa.publishable)
        self.assertEqual(row["entry_count"], len(entries))

    def test_lenient_and_strict_differ(self):
        rows = self._by_name(
            compare_profiles(VAGUE, "csv", profiles=["strict", "internal"])
        )
        # strict promotes the vague benefit to high; internal keeps it medium.
        self.assertEqual(rows["strict"]["counts"]["high"], 1)
        self.assertFalse(rows["strict"]["publishable"])
        self.assertEqual(rows["internal"]["counts"]["high"], 0)
        self.assertTrue(rows["internal"]["publishable"])
        self.assertNotEqual(rows["strict"]["delta"], rows["internal"]["delta"])

    def test_banned_phrase_differs_between_profiles(self):
        rows = self._by_name(
            compare_profiles(
                HYPE, "csv", profiles=["marketing-safe", "internal"]
            )
        )
        self.assertEqual(rows["marketing-safe"]["banned_hits"], 1)
        self.assertEqual(rows["internal"]["banned_hits"], 0)
        self.assertNotEqual(rows["marketing-safe"], rows["internal"])

    def test_flag_counts_and_severities(self):
        rows = self._by_name(
            compare_profiles(VAGUE, "csv", profiles=["strict"])
        )
        row = rows["strict"]
        self.assertEqual(row["flag_counts"]["vague_language"], 1)
        self.assertEqual(row["flag_severities"]["vague_language"], "high")
        self.assertIn("missing_required_section", row["flag_counts"])

    def test_baseline_delta_is_zero(self):
        report = compare_profiles(VAGUE, "csv")
        baseline = report["baseline"]
        first = report["profiles"][0]
        self.assertEqual(first["name"], baseline)
        self.assertEqual(
            first["delta"], {"high": 0, "total": 0, "sourced_ratio": 0.0}
        )

    def test_delta_is_signed_against_baseline(self):
        report = compare_profiles(
            VAGUE, "csv", profiles=["strict", "internal"]
        )
        strict, internal = report["profiles"]
        self.assertEqual(
            strict["delta"], {"high": 0, "total": 0, "sourced_ratio": 0.0}
        )
        self.assertEqual(internal["delta"]["high"], -1)
        self.assertEqual(internal["delta"]["total"], -1)
        self.assertEqual(internal["delta"]["sourced_ratio"], 0.0)

    def test_inline_config_layers_on_every_profile(self):
        report = compare_profiles(
            VAGUE,
            "csv",
            profiles=["internal", "marketing-safe"],
            config={"banned_phrases": ["better"]},
        )
        for row in report["profiles"]:
            self.assertEqual(row["banned_hits"], 1)

    def test_recommendation_picks_strictest_clean_profile(self):
        report = compare_profiles(CLEAN, "csv")
        self.assertEqual(report["recommendation"]["profile"], "strict")
        self.assertIn("0", report["recommendation"]["reason"])

    def test_recommendation_reason_names_winning_metrics(self):
        report = compare_profiles(VAGUE, "csv")
        best = report["recommendation"]["profile"]
        row = self._by_name(report)[best]
        reason = report["recommendation"]["reason"]
        self.assertIn(str(row["counts"]["high"]), reason)
        self.assertIn(str(row["counts"]["total"]), reason)
        self.assertIn("{:.2f}".format(row["sourced_ratio"]), reason)

    def test_recommendation_avoids_non_publishable(self):
        report = compare_profiles(VAGUE, "csv", profiles=["strict", "internal"])
        self.assertEqual(report["recommendation"]["profile"], "internal")

    def test_unknown_profile_raises(self):
        with self.assertRaises(UnknownProfileError):
            compare_profiles(VAGUE, "csv", profiles=["nope"])

    def test_non_list_profiles_rejected(self):
        with self.assertRaises(ValueError):
            compare_profiles(VAGUE, "csv", profiles="strict")

    def test_empty_profiles_rejected(self):
        with self.assertRaises(ValueError):
            compare_profiles(VAGUE, "csv", profiles=[])

    def test_unparseable_raises_parse_error(self):
        with self.assertRaises(ParseError):
            compare_profiles(UNPARSEABLE, "csv")

    def test_blank_content_rejected(self):
        with self.assertRaises(ValueError):
            compare_profiles("   ", "csv")


class ProfileComparisonExportTests(unittest.TestCase):
    def _report(self):
        return compare_profiles(VAGUE, "csv")

    def test_csv_header_and_rows(self):
        rows = list(csv.reader(io.StringIO(profile_comparison_csv(self._report()))))
        self.assertEqual(rows[0], list(PROFILE_COMPARISON_HEADER))
        self.assertEqual(
            rows[0],
            [
                "profile",
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
        self.assertEqual(len(rows), 1 + len(BUILTIN_PROFILES))
        self.assertEqual(rows[1][0], list(BUILTIN_PROFILES)[0])

    def test_csv_without_profiles_is_header_only(self):
        self.assertEqual(
            profile_comparison_csv({}), ",".join(PROFILE_COMPARISON_HEADER) + "\n"
        )

    def test_csv_round_trips_special_fields(self):
        report = {
            "profiles": [
                {
                    "name": 'café, "q"',
                    "entry_count": 1,
                    "counts": {"high": 1, "medium": 0, "low": 0, "total": 1},
                    "publishable": False,
                    "sourced_ratio": 0.5,
                    "vague_hits": 0,
                    "banned_hits": 0,
                }
            ]
        }
        rows = list(csv.reader(io.StringIO(profile_comparison_csv(report))))
        self.assertEqual(rows[1][0], 'café, "q"')
        self.assertEqual(rows[1][6], "False")
        self.assertEqual(rows[1][7], "0.5")

    def test_csv_uses_lf_endings(self):
        self.assertNotIn("\r\n", profile_comparison_csv(self._report()))

    def test_json_is_stable_and_nan_free(self):
        report = self._report()
        text = profile_comparison_json(report)
        self.assertEqual(text, profile_comparison_json(report))
        self.assertNotIn("NaN", text)
        self.assertEqual(
            text,
            json.dumps(
                report, ensure_ascii=False, sort_keys=True, allow_nan=False
            ),
        )
        self.assertEqual(len(json.loads(text)["profiles"]), len(BUILTIN_PROFILES))


class BuildProfileComparisonTests(unittest.TestCase):
    def test_requires_content(self):
        with self.assertRaises(ValueError):
            build_profile_comparison({"format": "csv"})

    def test_rejects_non_object_payload(self):
        with self.assertRaises(ValueError):
            build_profile_comparison(["nope"])

    def test_passes_profiles_and_config(self):
        report = build_profile_comparison(
            {
                "content": VAGUE,
                "format": "csv",
                "profiles": ["internal"],
                "config": {"banned_phrases": ["better"]},
            }
        )
        self.assertEqual([row["name"] for row in report["profiles"]], ["internal"])
        self.assertEqual(report["profiles"][0]["banned_hits"], 1)

    def test_sample_is_valid(self):
        report = build_profile_comparison(sample_profile_comparison())
        self.assertEqual(report["baseline"], list(BUILTIN_PROFILES)[0])
        self.assertEqual(len(report["profiles"]), len(BUILTIN_PROFILES))


class _ProfileServerMixin:
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


class ProfileCompareServerTests(_ProfileServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._start()

    @classmethod
    def tearDownClass(cls):
        cls._stop()

    def test_happy_path_returns_one_row_per_builtin(self):
        status, report = self._post(
            "/api/compare-profiles", {"format": "csv", "content": VAGUE}
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(report["profiles"]), len(BUILTIN_PROFILES))
        self.assertIn("baseline", report)
        self.assertIn("recommendation", report)
        for key in (
            "name",
            "counts",
            "publishable",
            "sourced_ratio",
            "vague_hits",
            "banned_hits",
            "flag_counts",
            "flag_severities",
        ):
            self.assertIn(key, report["profiles"][0])

    def test_marketing_safe_differs_from_internal(self):
        status, report = self._post(
            "/api/compare-profiles",
            {
                "format": "csv",
                "content": HYPE,
                "profiles": ["marketing-safe", "internal"],
            },
        )
        self.assertEqual(status, 200)
        rows = {row["name"]: row for row in report["profiles"]}
        self.assertEqual(rows["marketing-safe"]["banned_hits"], 1)
        self.assertEqual(rows["internal"]["banned_hits"], 0)

    def test_unknown_profile_is_400(self):
        status, body = self._post(
            "/api/compare-profiles",
            {"format": "csv", "content": VAGUE, "profiles": ["nope"]},
        )
        self.assertEqual(status, 400)
        self.assertIn("nope", body["error"])

    def test_non_list_profiles_is_400(self):
        status, body = self._post(
            "/api/compare-profiles",
            {"format": "csv", "content": VAGUE, "profiles": "strict"},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_invalid_config_is_400(self):
        status, body = self._post(
            "/api/compare-profiles",
            {"format": "csv", "content": VAGUE, "config": {"nope": []}},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_missing_content_is_400(self):
        status, body = self._post("/api/compare-profiles", {"format": "csv"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_unparseable_is_422(self):
        status, body = self._post(
            "/api/compare-profiles", {"format": "csv", "content": UNPARSEABLE}
        )
        self.assertEqual(status, 422)
        self.assertIn("error", body)

    def test_oversized_body_is_413(self):
        huge = "x" * (2 * 1024 * 1024 + 10)
        status, _body = self._post(
            "/api/compare-profiles", {"format": "csv", "content": huge}
        )
        self.assertEqual(status, 413)

    def test_sample_endpoint_is_valid(self):
        status, _headers, body = self._get("/api/compare-profiles/sample")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("content", payload)
        status, report = self._post("/api/compare-profiles", payload)
        self.assertEqual(status, 200)
        self.assertEqual(len(report["profiles"]), len(BUILTIN_PROFILES))

    def test_csv_export(self):
        params = [
            ("format", "csv"),
            ("content", HYPE),
            ("profile", "marketing-safe"),
            ("profile", "internal"),
        ]
        query = urllib.parse.urlencode(params)
        status, headers, body = self._get(
            "/api/export/profile-comparison.csv?" + query
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        self.assertEqual(rows[0], list(PROFILE_COMPARISON_HEADER))
        self.assertEqual([row[0] for row in rows[1:]], ["marketing-safe", "internal"])

    def test_json_export(self):
        params = [
            ("format", "csv"),
            ("content", VAGUE),
            ("profile", "strict"),
        ]
        query = urllib.parse.urlencode(params)
        status, headers, body = self._get(
            "/api/export/profile-comparison.json?" + query
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertNotIn("NaN", body.decode("utf-8"))
        report = json.loads(body.decode("utf-8"))
        self.assertEqual(len(report["profiles"]), 1)

    def test_export_accepts_config_query_param(self):
        params = [
            ("format", "csv"),
            ("content", VAGUE),
            ("profile", "internal"),
            ("config", json.dumps({"banned_phrases": ["better"]})),
        ]
        query = urllib.parse.urlencode(params)
        status, _headers, body = self._get(
            "/api/export/profile-comparison.csv?" + query
        )
        self.assertEqual(status, 200)
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        self.assertEqual(rows[1][9], "1")  # banned_hits

    def test_export_bad_config_is_400(self):
        params = [
            ("format", "csv"),
            ("content", VAGUE),
            ("config", "{not json"),
        ]
        query = urllib.parse.urlencode(params)
        status, _headers, body = self._get(
            "/api/export/profile-comparison.csv?" + query
        )
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)

    def test_export_unknown_profile_is_400(self):
        params = [("format", "csv"), ("content", VAGUE), ("profile", "nope")]
        query = urllib.parse.urlencode(params)
        status, _headers, body = self._get(
            "/api/export/profile-comparison.csv?" + query
        )
        self.assertEqual(status, 400)
        self.assertIn(b"nope", body)

    def test_export_missing_content_is_400(self):
        query = urllib.parse.urlencode([("format", "csv")])
        status, _headers, body = self._get(
            "/api/export/profile-comparison.csv?" + query
        )
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)


class ProfileCompareHistoryTests(_ProfileServerMixin, unittest.TestCase):
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

    def test_failed_and_successful_calls_do_not_store(self):
        before = len(self._history())
        for payload in (
            {"format": "csv", "content": VAGUE, "profiles": ["nope"]},
            {"format": "csv", "content": UNPARSEABLE},
            {"format": "csv", "content": VAGUE},
        ):
            self._post("/api/compare-profiles", payload)
        self.assertEqual(len(self._history()), before)


if __name__ == "__main__":
    unittest.main()
