"""Offline tests for the deterministic language lint (``releasenote.language``).

Covers the readability math, every flag code firing and not firing,
``severity_overrides`` re-ranking, empty/malformed input safety, the CSV/JSON
exporters, the bounded score penalty and the HTTP routes.
"""

import csv
import io
import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from releasenote.config import WorkspaceConfig
from releasenote.exporters import LANGUAGE_HEADER, language_csv, language_json
from releasenote.language import (
    ALL_CAPS,
    DUPLICATE_HEADING,
    EMOJI,
    FIRST_PERSON,
    JARGON,
    LANGUAGE_FLAG_CODES,
    LONG_SENTENCE,
    PASSIVE_VOICE,
    TENSE_INCONSISTENCY,
    compute_language,
    count_syllables,
    split_sentences,
)
from releasenote.models import ChangeEntry, Flag, QAResult
from releasenote.score import (
    BAND_READY,
    BAND_REVIEW,
    LANGUAGE_PENALTY_CODE,
    LANGUAGE_PENALTY_MAX,
    compute_score,
)
from releasenote.server import create_server
from releasenote.service import build_diff, build_draft


def _codes(report):
    return [finding["code"] for finding in report["findings"]]


def _entry(**kwargs):
    values = {
        "id": "A",
        "type": "fix",
        "title": "Fix login",
        "description": "Login no longer fails",
        "benefit": "Reliable access",
        "source": "A",
    }
    values.update(kwargs)
    return ChangeEntry(**values)


class ReadabilityTests(unittest.TestCase):
    def test_split_sentences(self):
        self.assertEqual(
            split_sentences("Hello world. This is a test! Really?"),
            ["Hello world.", "This is a test!", "Really?"],
        )
        self.assertEqual(split_sentences(""), [])
        self.assertEqual(split_sentences("no punctuation"), ["no punctuation"])

    def test_count_syllables(self):
        self.assertEqual(count_syllables("make"), 1)
        self.assertEqual(count_syllables("table"), 2)
        self.assertEqual(count_syllables("code"), 1)
        self.assertEqual(count_syllables("the"), 1)
        self.assertEqual(count_syllables(""), 0)

    def test_readability_math_on_known_sentences(self):
        report = compute_language(
            [_entry(title="", description="The cat sat. The dog ran.", benefit="")]
        )
        self.assertEqual(
            report["readability"],
            {
                "flesch_reading_ease": 119.19,
                "avg_sentence_length": 3.0,
                "avg_syllables_per_word": 1.0,
                "long_sentence_ratio": 0.0,
            },
        )

    def test_readability_zero_without_text(self):
        report = compute_language([])
        self.assertEqual(
            report["readability"],
            {
                "flesch_reading_ease": 0.0,
                "avg_sentence_length": 0.0,
                "avg_syllables_per_word": 0.0,
                "long_sentence_ratio": 0.0,
            },
        )


class FlagTests(unittest.TestCase):
    def test_long_sentence_fires_and_not(self):
        long_text = " ".join(["word"] * 45)
        fired = compute_language([_entry(description=long_text)])
        self.assertIn(LONG_SENTENCE, _codes(fired))
        self.assertEqual(fired["readability"]["long_sentence_ratio"], 1.0)
        quiet = compute_language([_entry(description="Short and clear.")])
        self.assertNotIn(LONG_SENTENCE, _codes(quiet))

    def test_passive_voice_fires_and_not(self):
        self.assertIn(
            PASSIVE_VOICE,
            _codes(compute_language([_entry(description="The bug was fixed by the team.")])),
        )
        self.assertNotIn(
            PASSIVE_VOICE,
            _codes(compute_language([_entry(description="The team fixed the bug.")])),
        )

    def test_first_person_fires_and_not(self):
        self.assertIn(
            FIRST_PERSON,
            _codes(compute_language([_entry(description="We are excited to ship this.")])),
        )
        self.assertNotIn(
            FIRST_PERSON,
            _codes(compute_language([_entry(description="The team ships this.")])),
        )

    def test_all_caps_fires_and_ignores_acronyms(self):
        self.assertIn(
            ALL_CAPS, _codes(compute_language([_entry(title="SHIP IT")]))
        )
        self.assertNotIn(
            ALL_CAPS, _codes(compute_language([_entry(title="Ship it")]))
        )
        self.assertNotIn(
            ALL_CAPS, _codes(compute_language([_entry(title="HTTP and JSON support")]))
        )

    def test_emoji_fires_and_not(self):
        self.assertIn(
            EMOJI, _codes(compute_language([_entry(benefit="Great job \U0001F680")]))
        )
        self.assertNotIn(
            EMOJI, _codes(compute_language([_entry(benefit="Great job")]))
        )

    def test_jargon_fires_and_not(self):
        self.assertIn(
            JARGON,
            _codes(compute_language([_entry(description="We leverage the platform.")])),
        )
        self.assertNotIn(
            JARGON,
            _codes(compute_language([_entry(description="We use the platform.")])),
        )

    def test_tense_inconsistency_fires_and_not(self):
        mixed = compute_language(
            [
                _entry(id="A", benefit="Added one-click export."),
                _entry(id="B", title="Second", benefit="Saves time for admins."),
            ]
        )
        self.assertIn(TENSE_INCONSISTENCY, _codes(mixed))
        self.assertGreater(mixed["style"]["tense_mix"], 0)

        consistent = compute_language(
            [
                _entry(id="A", benefit="Saves time."),
                _entry(id="B", title="Second", benefit="Keeps data safe."),
            ]
        )
        self.assertNotIn(TENSE_INCONSISTENCY, _codes(consistent))

    def test_duplicate_heading_fires_and_not(self):
        duplicates = compute_language(
            [
                _entry(id="A", title="Bulk export"),
                _entry(id="B", title="Bulk export"),
            ]
        )
        self.assertIn(DUPLICATE_HEADING, _codes(duplicates))

        unique = compute_language(
            [
                _entry(id="A", title="Bulk export"),
                _entry(id="B", title="Search filters"),
            ]
        )
        self.assertNotIn(DUPLICATE_HEADING, _codes(unique))

    def test_style_counters(self):
        report = compute_language(
            [_entry(description="We leverage the platform and are excited.")]
        )
        style = report["style"]
        self.assertGreaterEqual(style["first_person_hits"], 1)
        self.assertGreaterEqual(style["jargon_hits"], 1)
        self.assertGreaterEqual(style["passive_voice_hits"], 1)
        self.assertEqual(
            set(style),
            {
                "passive_voice_hits",
                "first_person_hits",
                "all_caps_hits",
                "emoji_hits",
                "jargon_hits",
                "tense_mix",
            },
        )


class SeverityOverrideTests(unittest.TestCase):
    def test_override_reranks_language_flag(self):
        config = WorkspaceConfig.from_dict(
            {"severity_overrides": {"jargon": "high"}}
        )
        report = compute_language(
            [_entry(description="We leverage the platform.")], config=config
        )
        jargon = next(f for f in report["findings"] if f["code"] == JARGON)
        self.assertEqual(jargon["severity"], "high")
        self.assertEqual(report["summary"]["counts"]["high"], 1)

    def test_every_code_honours_override(self):
        from releasenote.service import sample_language

        sample = sample_language()
        overrides = {code: "high" for code in LANGUAGE_FLAG_CODES}
        config = WorkspaceConfig.from_dict({"severity_overrides": overrides})
        report = compute_language(
            build_draft(sample["content"], sample["format"])["entries"],
            config=config,
        )
        present = {finding["code"] for finding in report["findings"]}
        self.assertEqual(present, set(LANGUAGE_FLAG_CODES))
        self.assertTrue(
            all(finding["severity"] == "high" for finding in report["findings"])
        )

    def test_default_severities(self):
        report = compute_language([_entry(title="SHIP IT")])
        finding = next(f for f in report["findings"] if f["code"] == ALL_CAPS)
        self.assertEqual(finding["severity"], "low")
        long_report = compute_language(
            [_entry(description=" ".join(["word"] * 45))]
        )
        long_finding = next(
            f for f in long_report["findings"] if f["code"] == LONG_SENTENCE
        )
        self.assertEqual(long_finding["severity"], "medium")


class SafetyTests(unittest.TestCase):
    def test_empty_and_falsy_inputs(self):
        for value in ([], None, "", "not a list", 42):
            report = compute_language(value)
            self.assertEqual(report["summary"]["counts"]["total"], 0)
            self.assertEqual(report["summary"]["grade"], "clear")
            self.assertEqual(report["findings"], [])

    def test_malformed_entries_do_not_raise(self):
        report = compute_language([None, 42, {}, _entry()])
        self.assertIsInstance(report["findings"], list)

    def test_blank_entry_reports_zero_findings(self):
        report = compute_language([ChangeEntry()])
        self.assertEqual(report["summary"]["counts"]["total"], 0)
        self.assertEqual(report["summary"]["grade"], "clear")

    def test_json_serialisable_and_nan_free(self):
        report = compute_language([_entry(description="We are excited.")])
        text = json.dumps(report, allow_nan=False)
        self.assertIn("findings", json.loads(text))


class ExporterTests(unittest.TestCase):
    def _payload(self, entries=None):
        return build_draft(
            "id,type,title,description,benefit,source\n"
            "A,fix,Fix login,We are excited to ship this.,Reliable access,A\n",
            "csv",
        )

    def test_csv_header_and_rows(self):
        payload = self._payload()
        rows = list(csv.reader(io.StringIO(language_csv(payload))))
        self.assertEqual(rows[0], list(LANGUAGE_HEADER))
        self.assertEqual(rows[0], ["code", "severity", "entry_id", "message"])
        self.assertEqual(len(rows), 1 + len(payload["language"]["findings"]))
        for finding, row in zip(payload["language"]["findings"], rows[1:]):
            self.assertEqual(
                row,
                [
                    finding["code"],
                    finding["severity"],
                    finding["entry_id"],
                    finding["message"],
                ],
            )

    def test_csv_missing_language_is_header_only(self):
        self.assertEqual(
            list(csv.reader(io.StringIO(language_csv({})))),
            [["code", "severity", "entry_id", "message"]],
        )

    def test_csv_round_trips_special_characters(self):
        payload = {
            "language": {
                "findings": [
                    {
                        "code": "jargon",
                        "severity": "medium",
                        "entry_id": "A,1",
                        "message": 'Says "leverage", really,\nin one line',
                    }
                ]
            }
        }
        rows = list(csv.reader(io.StringIO(language_csv(payload))))
        self.assertEqual(rows[1][2], "A,1")
        self.assertEqual(rows[1][3], 'Says "leverage", really,\nin one line')
        self.assertNotIn("\r\n", language_csv(payload))

    def test_json_matches_language_object_and_is_stable(self):
        payload = self._payload()
        decoded = json.loads(language_json(payload))
        self.assertEqual(decoded, payload["language"])
        self.assertEqual(
            language_json(payload),
            json.dumps(
                payload["language"],
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            ),
        )

    def test_json_missing_language_is_empty_object(self):
        self.assertEqual(json.loads(language_json({})), {})


class ScorePenaltyTests(unittest.TestCase):
    def _entry(self):
        return _entry()

    def test_language_penalty_is_bounded(self):
        entries = [self._entry()]
        flags = [
            Flag(code="jargon", severity="medium", message="x", entry_id="A")
            for _ in range(50)
        ]
        result = compute_score(QAResult(flags=flags), entries)
        language_rows = [
            p for p in result["penalties"] if p["code"] == LANGUAGE_PENALTY_CODE
        ]
        self.assertEqual(len(language_rows), 1)
        self.assertEqual(language_rows[0]["points"], float(LANGUAGE_PENALTY_MAX))
        self.assertEqual(language_rows[0]["count"], 50)

    def test_language_penalty_scales_per_finding(self):
        entries = [self._entry()]
        result = compute_score(
            QAResult(
                flags=[
                    Flag("jargon", "medium", "x", "A"),
                    Flag("passive_voice", "low", "y", "A"),
                ]
            ),
            entries,
        )
        row = next(
            p for p in result["penalties"] if p["code"] == LANGUAGE_PENALTY_CODE
        )
        self.assertEqual(row["points"], 4.0)
        self.assertEqual(row["count"], 2)

    def test_band_changes_when_language_findings_added(self):
        entries = [self._entry()]
        clean = compute_score(QAResult(), entries)
        self.assertEqual(clean["band"], BAND_READY)
        flags = [
            Flag(code="jargon", severity="medium", message="x", entry_id="A")
            for _ in range(8)
        ]
        noisy = compute_score(QAResult(flags=flags), entries)
        self.assertLess(noisy["score"], clean["score"])
        self.assertEqual(noisy["band"], BAND_REVIEW)

    def test_penalties_remain_explainable(self):
        entries = [self._entry()]
        flags = [
            Flag("missing_source", "high", "x", "A"),
            Flag("jargon", "medium", "y", "A"),
        ]
        result = compute_score(QAResult(flags=flags), entries)
        deducted = sum(p["points"] for p in result["penalties"])
        self.assertEqual(result["score"], int(round(100 - deducted)))

    def test_language_argument_controls_count(self):
        entries = [self._entry()]
        qa = QAResult()
        language = {
            "findings": [
                {"code": "jargon"},
                {"code": "passive_voice"},
                {"code": "all_caps"},
            ]
        }
        result = compute_score(qa, entries, language=language)
        row = next(
            p for p in result["penalties"] if p["code"] == LANGUAGE_PENALTY_CODE
        )
        self.assertEqual(row["count"], 3)
        self.assertEqual(row["points"], 6.0)

    def test_no_language_penalty_when_clean(self):
        result = compute_score(QAResult(), [self._entry()])
        self.assertEqual(result["penalties"], [])

    def test_gate_changes_with_language_findings(self):
        config = WorkspaceConfig.from_dict({"min_score": 90})
        entries = [self._entry()]
        clean = compute_score(QAResult(), entries, config)
        self.assertEqual(clean["gate"]["status"], "pass")
        flags = [
            Flag(code="jargon", severity="medium", message="x", entry_id="A")
            for _ in range(8)
        ]
        noisy = compute_score(QAResult(flags=flags), entries, config)
        self.assertEqual(noisy["gate"]["status"], "fail")
        self.assertTrue(noisy["gate"]["reasons"])


class DraftIntegrationTests(unittest.TestCase):
    def test_build_draft_has_language_and_merged_flags(self):
        result = build_draft(
            "id,type,title,description,benefit,source\n"
            "A,fix,Fix login,We are excited to ship this.,Reliable access,A\n",
            "csv",
        )
        language = result["language"]
        self.assertEqual(
            set(language),
            {"readability", "style", "findings", "summary"},
        )
        codes = {f["code"] for f in result["qa"]["flags"]}
        self.assertIn(FIRST_PERSON, codes)

    def test_acceptance_examples(self):
        long_text = " ".join(["word"] * 45)

        def codes_for(content):
            return {
                f["code"] for f in build_draft(content, "csv")["qa"]["flags"]
            }

        header = "id,type,title,description,benefit,source\n"
        self.assertIn(
            LONG_SENTENCE,
            codes_for(header + "A,fix,Fix login,{}.,Reliable,A\n".format(long_text)),
        )
        self.assertIn(
            FIRST_PERSON,
            codes_for(header + "A,fix,Fix login,We are excited.,Reliable,A\n"),
        )
        self.assertIn(
            ALL_CAPS, codes_for(header + "A,fix,SHIP IT,Clear win.,Reliable,A\n")
        )
        self.assertIn(
            JARGON,
            codes_for(header + "A,fix,Fix login,We leverage the platform.,Reliable,A\n"),
        )
        self.assertIn(
            TENSE_INCONSISTENCY,
            codes_for(
                header
                + "A,fix,First,Added one-click export.,Added it,A\n"
                + "B,fix,Second,Fixed the job.,Saves time,B\n"
            ),
        )
        self.assertIn(
            DUPLICATE_HEADING,
            codes_for(
                header
                + "A,fix,Bulk export,Export reports.,Cuts effort,A\n"
                + "B,fix,Bulk export,Export again.,Saves time,B\n"
            ),
        )

    def test_build_draft_honours_severity_override(self):
        result = build_draft(
            "id,type,title,description,benefit,source\n"
            "A,fix,Fix login,We leverage the platform.,Reliable,A\n",
            "csv",
            config={"severity_overrides": {"jargon": "high"}},
        )
        jargon = next(
            f for f in result["qa"]["flags"] if f["code"] == JARGON
        )
        self.assertEqual(jargon["severity"], "high")

    def test_build_diff_has_language(self):
        previous = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Fix login,Login works.,Reliable access,A\n"
        )
        current = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Fix login,We are excited to ship this.,Reliable access,A\n"
        )
        result = build_diff(previous, current, "csv")
        self.assertIn("language", result)
        self.assertIn(FIRST_PERSON, {f["code"] for f in result["qa"]["flags"]})


class LanguageServerTests(unittest.TestCase):
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

    def _get_raw(self, path, extra=None):
        params = {"format": "csv"}
        params.update(extra or {})
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(self.base + path + "?" + query)
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def _get_json(self, path):
        request = urllib.request.Request(self.base + path)
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read())

    def _post(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, json.loads(resp.read())

    CONTENT = (
        "id,type,title,description,benefit,source\n"
        "A,fix,Fix login,We are excited to ship this.,Reliable access,A\n"
    )

    def test_export_language_csv(self):
        status, headers, body = self._get_raw(
            "/api/export/language.csv", {"content": self.CONTENT}
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        self.assertEqual(rows[0], ["code", "severity", "entry_id", "message"])
        self.assertGreaterEqual(len(rows), 2)

    def test_export_language_json_matches_payload(self):
        status, headers, body = self._get_raw(
            "/api/export/language.json", {"content": self.CONTENT}
        )
        self.assertEqual(status, 200)
        self.assertTrue(
            headers["Content-Type"].startswith("application/json")
        )
        expected = build_draft(self.CONTENT, "csv")["language"]
        self.assertEqual(json.loads(body.decode("utf-8")), expected)

    def test_export_language_missing_content_is_400(self):
        status, _headers, body = self._get_raw("/api/export/language.csv")
        self.assertEqual(status, 400)
        self.assertIn("content", json.loads(body.decode("utf-8"))["error"])

    def test_export_language_unparseable_is_422(self):
        status, _headers, _body = self._get_raw(
            "/api/export/language.csv", {"content": "foo,bar\n1,2\n"}
        )
        self.assertEqual(status, 422)

    def test_language_sample_is_valid_generate_input(self):
        status, _headers, sample = self._get_json("/api/language/sample")
        self.assertEqual(status, 200)
        self.assertIn("content", sample)
        generated_status, result = self._post(
            "/api/generate",
            {"content": sample["content"], "format": sample.get("format", "csv")},
        )
        self.assertEqual(generated_status, 200)
        self.assertIn("language", result)
        self.assertGreater(result["language"]["summary"]["counts"]["total"], 0)
        codes = {f["code"] for f in result["qa"]["flags"]}
        self.assertTrue(set(LANGUAGE_FLAG_CODES) & codes)


if __name__ == "__main__":
    unittest.main()
