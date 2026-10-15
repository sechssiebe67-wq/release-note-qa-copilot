import csv
import io
import json
import unittest

from releasenote.exporters import (
    DIFF_HEADER,
    checklist_csv,
    diff_csv,
    draft_json,
    qa_flags_csv,
)
from releasenote.service import build_diff, build_draft, sample

PREVIOUS = (
    "id,type,title,description,benefit,source,link\n"
    "A,feature,Alpha,Alpha does things,Fast and reliable,SRC-A,https://a\n"
)
CURRENT = (
    "id,type,title,description,benefit,source,link\n"
    "A,feature,Alpha,Alpha does things,Reliable and fast,,\n"
    "B,fix,Beta,Beta description,Gamma value,SRC-B,\n"
)


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


class QaFlagsCsvTests(unittest.TestCase):
    def test_header_plus_one_row_per_flag(self):
        payload = build_draft(sample("csv")["content"], "csv")
        rows = _rows(qa_flags_csv(payload))
        self.assertEqual(rows[0], ["entry_id", "code", "severity", "message"])
        self.assertEqual(len(rows), 1 + len(payload["qa"]["flags"]))

    def test_code_and_severity_match_payload(self):
        payload = build_draft(sample("csv")["content"], "csv")
        rows = _rows(qa_flags_csv(payload))
        by_code = {row[1]: row[2] for row in rows[1:]}
        for flag in payload["qa"]["flags"]:
            self.assertEqual(by_code[flag["code"]], flag["severity"])

    def test_missing_qa_returns_header_only(self):
        rows = _rows(qa_flags_csv({}))
        self.assertEqual(rows, [["entry_id", "code", "severity", "message"]])

    def test_returns_string(self):
        self.assertIsInstance(qa_flags_csv(build_draft(sample("csv")["content"])), str)


class ChecklistCsvTests(unittest.TestCase):
    def test_header_and_rows(self):
        payload = build_draft(sample("csv")["content"], "csv")
        rows = _rows(checklist_csv(payload))
        self.assertEqual(rows[0], ["label", "status", "detail"])
        self.assertEqual(len(rows), 1 + len(payload["checklist"]))
        self.assertEqual(rows[1][0], payload["checklist"][0]["label"])


class DiffCsvTests(unittest.TestCase):
    def test_without_diff_returns_header_only(self):
        rows = _rows(diff_csv({"entries": []}))
        self.assertEqual(rows, [list(DIFF_HEADER)])

    def test_categories_and_columns(self):
        payload = build_diff(PREVIOUS, CURRENT, "csv")
        rows = _rows(diff_csv(payload))
        self.assertEqual(rows[0], ["category", "id", "title", "type", "reason"])
        categories = {row[0] for row in rows[1:]}
        self.assertTrue(categories <= {
            "added",
            "changed",
            "removed",
            "unchanged",
            "repeated_claims",
            "regressions",
        })
        total = sum(len(payload["diff"][key]) for key in payload["diff"])
        self.assertEqual(len(rows) - 1, total)


class CsvQuotingTests(unittest.TestCase):
    def test_round_trips_commas_quotes_and_newlines(self):
        payload = {
            "qa": {
                "flags": [
                    {
                        "entry_id": "A,1",
                        "code": "vague",
                        "severity": "high",
                        "message": 'Says "better", really,\nin one line',
                    }
                ]
            }
        }
        text = qa_flags_csv(payload)
        rows = _rows(text)
        self.assertEqual(rows[1][0], "A,1")
        self.assertEqual(rows[1][3], 'Says "better", really,\nin one line')

    def test_uses_lf_line_endings(self):
        self.assertNotIn("\r\n", qa_flags_csv(build_draft(sample("csv")["content"])))


class DraftJsonTests(unittest.TestCase):
    def test_round_trips_full_payload(self):
        payload = build_draft(sample("csv")["content"], "csv")
        decoded = json.loads(draft_json(payload))
        self.assertEqual(decoded["qa"], payload["qa"])
        self.assertEqual(decoded["checklist"], payload["checklist"])
        self.assertEqual(decoded["entries"], payload["entries"])

    def test_stable_key_order(self):
        payload = build_draft(sample("csv")["content"], "csv")
        text = draft_json(payload)
        self.assertEqual(text, json.dumps(
            payload, ensure_ascii=False, sort_keys=True, allow_nan=False
        ))

    def test_utf8_content_is_preserved(self):
        payload = {"title": "Version \u00e9t\u00e9", "entries": []}
        decoded = json.loads(draft_json(payload))
        self.assertEqual(decoded["title"], "Version \u00e9t\u00e9")


if __name__ == "__main__":
    unittest.main()
