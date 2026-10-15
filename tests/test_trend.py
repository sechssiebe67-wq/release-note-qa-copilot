import csv
import io
import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from releasenote.config import WorkspaceConfig
from releasenote.exporters import TREND_HEADER, trend_csv, trend_json
from releasenote.importer import ParseError
from releasenote.profiles import UnknownProfileError, resolve_config
from releasenote.server import create_server
from releasenote.service import build_draft, build_trend, sample_trend
from releasenote.trend import _compare, compute_trend

# Release payloads, simplest first: BAD has high + medium flags, MEDIUM_ONLY
# has only medium flags, CLEAN has no flags.
BAD = (
    "id,type,title,description,benefit,source\n"
    "REL-1,feature,Bulk export,Export all reports in one click,"
    "A much better experience,\n"
    "REL-2,feature,Better search,Search is improved,Find things faster,\n"
)
MEDIUM_ONLY = (
    "id,type,title,description,benefit,source\n"
    "REL-1,fix,Fix timezone,Timestamps render locally,"
    "Correct local times,REL-1\n"
    "REL-2,feature,Better search,Search is improved,"
    "Find things faster,REL-2\n"
)
CLEAN = (
    "id,type,title,description,benefit,source\n"
    "REL-1,fix,Fix timezone,Timestamps render locally,"
    "Correct local times,REL-1\n"
    "REL-2,feature,Search filters,Filter results by team,"
    "Relevant results only,REL-2\n"
)
VAGUE = (
    "id,type,title,description,benefit,source\n"
    "A,fix,Better sync,The sync is improved,A much better experience,A\n"
)
HALF_SOURCED = (
    "id,type,title,description,benefit,source\n"
    "A,fix,Fix login,Login no longer fails,Reliable access,A\n"
    "B,fix,Fix signup,Signup no longer fails,Reliable signup,\n"
)
UNPARSEABLE = "foo,bar\n1,2\n"


def _release(label, content):
    return {"label": label, "content": content, "format": "csv"}


class ComputeTrendTests(unittest.TestCase):
    def test_shape(self):
        report = compute_trend(
            [_release("r1", CLEAN), _release("r2", CLEAN), _release("r3", CLEAN)]
        )
        self.assertEqual(set(report), {"releases", "deltas", "overall"})
        self.assertEqual(len(report["releases"]), 3)
        self.assertEqual(len(report["deltas"]), 2)
        self.assertEqual(set(report["overall"]), {"verdict", "best", "worst"})
        for release in report["releases"]:
            self.assertEqual(
                set(release),
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
        for delta in report["deltas"]:
            self.assertEqual(
                set(delta),
                {"from", "to", "high", "medium", "total", "sourced_ratio", "verdict"},
            )

    def test_counts_and_metrics_match_single_draft(self):
        report = compute_trend([_release("r1", BAD), _release("r2", CLEAN)])
        release = report["releases"][0]
        draft = build_draft(BAD, "csv")
        self.assertEqual(release["counts"], draft["qa"]["counts"])
        self.assertEqual(release["entry_count"], draft["entry_count"])
        self.assertEqual(release["publishable"], draft["qa"]["publishable"])
        self.assertEqual(release["vague_hits"], 2)
        self.assertEqual(release["banned_hits"], 0)

    def test_sourced_ratio(self):
        report = compute_trend(
            [_release("r1", HALF_SOURCED), _release("r2", HALF_SOURCED)]
        )
        self.assertEqual(report["releases"][0]["sourced_ratio"], 0.5)
        self.assertEqual(report["releases"][1]["sourced_ratio"], 0.5)

    def test_sourced_ratio_one_and_zero(self):
        report = compute_trend([_release("r1", CLEAN), _release("r2", BAD)])
        self.assertEqual(report["releases"][0]["sourced_ratio"], 1.0)
        self.assertEqual(report["releases"][1]["sourced_ratio"], 0.0)

    def test_banned_hits(self):
        content = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Sync,Sync is guaranteed,Guaranteed results,A\n"
        )
        report = compute_trend(
            [_release("r1", content), _release("r2", CLEAN)],
            config={"banned_phrases": ["guaranteed"]},
        )
        self.assertEqual(report["releases"][0]["banned_hits"], 1)

    def test_delta_values(self):
        report = compute_trend([_release("r1", BAD), _release("r2", CLEAN)])
        delta = report["deltas"][0]
        self.assertEqual(delta["from"], "r1")
        self.assertEqual(delta["to"], "r2")
        self.assertEqual(delta["high"], -2)
        self.assertEqual(delta["medium"], -2)
        self.assertEqual(delta["total"], -4)
        self.assertEqual(delta["sourced_ratio"], 1.0)


class VerdictTests(unittest.TestCase):
    def test_improving(self):
        report = compute_trend([_release("r1", BAD), _release("r2", CLEAN)])
        self.assertEqual(report["deltas"][0]["verdict"], "improving")
        self.assertEqual(report["overall"]["verdict"], "improving")

    def test_regressing(self):
        report = compute_trend([_release("r1", CLEAN), _release("r2", BAD)])
        self.assertEqual(report["deltas"][0]["verdict"], "regressing")
        self.assertEqual(report["overall"]["verdict"], "regressing")

    def test_stable(self):
        report = compute_trend([_release("r1", CLEAN), _release("r2", CLEAN)])
        self.assertEqual(report["deltas"][0]["verdict"], "stable")
        self.assertEqual(report["overall"]["verdict"], "stable")

    def test_uses_total_when_high_equal(self):
        report = compute_trend(
            [_release("r1", MEDIUM_ONLY), _release("r2", CLEAN)]
        )
        self.assertEqual(report["releases"][0]["counts"]["high"], 0)
        self.assertEqual(report["deltas"][0]["verdict"], "improving")

    def test_uses_sourced_ratio_when_high_and_total_equal(self):
        first = {"counts": {"high": 0, "total": 0}, "sourced_ratio": 0.0}
        second = {"counts": {"high": 0, "total": 0}, "sourced_ratio": 1.0}
        self.assertEqual(_compare(first, second), "improving")
        self.assertEqual(_compare(second, first), "regressing")
        self.assertEqual(_compare(first, first), "stable")

    def test_best_and_worst(self):
        report = compute_trend(
            [
                _release("r1", BAD),
                _release("r2", MEDIUM_ONLY),
                _release("r3", CLEAN),
            ]
        )
        self.assertEqual(report["overall"]["best"], "r3")
        self.assertEqual(report["overall"]["worst"], "r1")


class ValidationTests(unittest.TestCase):
    def test_rejects_zero_one_and_twenty_one_releases(self):
        for count in (0, 1, 21):
            releases = [_release("r{}".format(i), CLEAN) for i in range(count)]
            with self.assertRaises(ValueError):
                compute_trend(releases)

    def test_accepts_two_and_twenty_releases(self):
        for count in (2, 20):
            releases = [_release("r{}".format(i), CLEAN) for i in range(count)]
            report = compute_trend(releases)
            self.assertEqual(len(report["releases"]), count)

    def test_rejects_non_list(self):
        with self.assertRaises(ValueError):
            compute_trend("not a list")

    def test_rejects_non_object_release(self):
        with self.assertRaises(ValueError):
            compute_trend(["nope", _release("r2", CLEAN)])

    def test_rejects_empty_content(self):
        with self.assertRaises(ValueError):
            compute_trend([_release("r1", "   "), _release("r2", CLEAN)])

    def test_missing_label_gets_default(self):
        report = compute_trend(
            [
                {"content": CLEAN, "format": "csv"},
                {"content": CLEAN, "format": "csv"},
            ]
        )
        self.assertEqual(
            [item["label"] for item in report["releases"]],
            ["Release 1", "Release 2"],
        )

    def test_unparseable_content_raises_parse_error(self):
        with self.assertRaises(ParseError):
            compute_trend([_release("r1", UNPARSEABLE), _release("r2", CLEAN)])

    def test_unknown_profile_raises(self):
        with self.assertRaises(UnknownProfileError):
            compute_trend([_release("r1", CLEAN), _release("r2", CLEAN)],
                          profile="nope")


class SeverityOverrideTrendTests(unittest.TestCase):
    def test_inline_config_reranks_counts(self):
        default = compute_trend([_release("r1", VAGUE), _release("r2", VAGUE)])
        self.assertEqual(default["releases"][0]["counts"]["high"], 0)
        self.assertEqual(default["releases"][0]["counts"]["medium"], 1)

        overridden = compute_trend(
            [_release("r1", VAGUE), _release("r2", VAGUE)],
            config={"severity_overrides": {"vague_language": "high"}},
        )
        self.assertEqual(overridden["releases"][0]["counts"]["high"], 1)
        self.assertEqual(overridden["releases"][0]["counts"]["medium"], 0)

    def test_profile_reranks_counts(self):
        strict = compute_trend(
            [_release("r1", VAGUE), _release("r2", VAGUE)], profile="strict"
        )
        self.assertEqual(strict["releases"][0]["counts"]["high"], 1)
        # Matches the single-draft QA result for the same content and profile.
        workspace = resolve_config({"profile": "strict"})
        draft = build_draft(VAGUE, "csv", config=workspace)
        self.assertEqual(strict["releases"][0]["counts"], draft["qa"]["counts"])


class BuildTrendTests(unittest.TestCase):
    def test_adds_config_profile_and_rules(self):
        report = build_trend(
            {
                "releases": [_release("r1", VAGUE), _release("r2", CLEAN)],
                "profile": "strict",
            }
        )
        self.assertEqual(report["profile"], "strict")
        self.assertEqual(
            report["config"]["severity_overrides"].get("vague_language"), "high"
        )
        self.assertIn("rules", report)
        self.assertEqual(
            report["rules"]["severity_overrides"].get("vague_language"), "high"
        )

    def test_default_config_echoed(self):
        report = build_trend(
            {"releases": [_release("r1", CLEAN), _release("r2", CLEAN)]}
        )
        self.assertIsNone(report["profile"])
        self.assertEqual(report["config"], WorkspaceConfig.default().to_dict())

    def test_rejects_bad_payload(self):
        with self.assertRaises(ValueError):
            build_trend([_release("r1", CLEAN)])
        with self.assertRaises(ValueError):
            build_trend({"releases": [_release("r1", CLEAN)]})

    def test_unknown_profile_raises(self):
        with self.assertRaises(UnknownProfileError):
            build_trend(
                {"releases": [_release("r1", CLEAN), _release("r2", CLEAN)],
                 "profile": "nope"}
            )

    def test_sample_is_valid(self):
        report = build_trend(sample_trend())
        self.assertEqual(len(report["releases"]), 3)
        self.assertEqual(len(report["deltas"]), 2)


class TrendExportTests(unittest.TestCase):
    def _report(self):
        return compute_trend(
            [_release("r1", BAD), _release("r2", MEDIUM_ONLY),
             _release("r3", CLEAN)]
        )

    def test_csv_header_and_rows(self):
        rows = list(csv.reader(io.StringIO(trend_csv(self._report()))))
        self.assertEqual(rows[0], list(TREND_HEADER))
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
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1][0], "r1")
        self.assertEqual(rows[1][2], "2")
        self.assertEqual(rows[1][5], "4")

    def test_csv_is_stable(self):
        report = self._report()
        self.assertEqual(trend_csv(report), trend_csv(report))

    def test_csv_without_releases_is_header_only(self):
        self.assertEqual(trend_csv({}), ",".join(TREND_HEADER) + "\n")

    def test_json_is_stable_and_nan_free(self):
        report = self._report()
        text = trend_json(report)
        self.assertEqual(text, trend_json(report))
        self.assertNotIn("NaN", text)
        self.assertEqual(
            text,
            json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False),
        )
        self.assertEqual(len(json.loads(text)["releases"]), 3)


class _TrendServerMixin:
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
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return resp.status, resp.headers, resp.read()


class TrendServerTests(_TrendServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._start()

    @classmethod
    def tearDownClass(cls):
        cls._stop()

    def test_trend_happy_path(self):
        payload = {
            "releases": [
                _release("r1", BAD),
                _release("r2", MEDIUM_ONLY),
                _release("r3", CLEAN),
            ]
        }
        status, report = self._post("/api/trend", payload)
        self.assertEqual(status, 200)
        self.assertEqual(len(report["releases"]), 3)
        self.assertEqual(len(report["deltas"]), 2)
        self.assertIn("overall", report)
        for key in ("label", "entry_count", "counts", "publishable",
                    "sourced_ratio", "vague_hits", "banned_hits"):
            self.assertIn(key, report["releases"][0])
        self.assertIn("config", report)
        self.assertIn("rules", report)

    def test_trend_release_bounds_are_400(self):
        for count in (0, 1, 21):
            releases = [_release("r{}".format(i), CLEAN) for i in range(count)]
            status, body = self._post("/api/trend", {"releases": releases})
            self.assertEqual(status, 400, count)
            self.assertIn("error", body)

    def test_trend_unknown_profile_is_400(self):
        status, body = self._post(
            "/api/trend",
            {"releases": [_release("r1", CLEAN), _release("r2", CLEAN)],
             "profile": "nope"},
        )
        self.assertEqual(status, 400)
        self.assertIn("nope", body["error"])

    def test_trend_unparseable_is_422(self):
        status, body = self._post(
            "/api/trend",
            {"releases": [_release("r1", UNPARSEABLE), _release("r2", CLEAN)]},
        )
        self.assertEqual(status, 422)
        self.assertIn("error", body)

    def test_trend_missing_content_is_400(self):
        status, body = self._post(
            "/api/trend",
            {"releases": [{"label": "r1"}, _release("r2", CLEAN)]},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_sample_endpoint_is_valid(self):
        status, _headers, body = self._get("/api/trend/sample")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        status, report = self._post("/api/trend", payload)
        self.assertEqual(status, 200)
        self.assertEqual(len(report["releases"]), 3)

    def _get_export(self, path, params):
        query = urllib.parse.urlencode(params)
        try:
            return self._get(path + "?" + query)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_trend_csv_export(self):
        params = [
            ("format", "csv"),
            ("release", "2026.1:" + BAD),
            ("release", "2026.2:" + CLEAN),
        ]
        status, headers, body = self._get_export("/api/export/trend.csv", params)
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        self.assertEqual(rows[0], list(TREND_HEADER))
        self.assertEqual(len(rows), 3)

    def test_trend_json_export(self):
        params = [
            ("format", "csv"),
            ("release", "2026.1:" + BAD),
            ("release", "2026.2:" + CLEAN),
        ]
        status, headers, body = self._get_export("/api/export/trend.json", params)
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("attachment", headers["Content-Disposition"])
        report = json.loads(body.decode("utf-8"))
        self.assertEqual(len(report["releases"]), 2)

    def test_trend_export_requires_two_releases(self):
        status, _headers, body = self._get_export(
            "/api/export/trend.csv", [("release", "only:" + CLEAN)]
        )
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)

    def test_trend_export_unknown_profile_is_400(self):
        params = [
            ("release", "r1:" + CLEAN),
            ("release", "r2:" + CLEAN),
            ("profile", "nope"),
        ]
        status, _headers, body = self._get_export("/api/export/trend.csv", params)
        self.assertEqual(status, 400)
        self.assertIn(b"nope", body)

    def test_trend_export_bad_release_param_is_400(self):
        params = [("release", "no-colon-here"), ("release", "r2:" + CLEAN)]
        status, _headers, body = self._get_export("/api/export/trend.csv", params)
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)


if __name__ == "__main__":
    unittest.main()
