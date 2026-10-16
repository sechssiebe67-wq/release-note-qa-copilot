"""Offline tests for the deterministic risk heatmap.

Every check is pure and offline. The heatmap is exercised both on hand-built
payloads (to pin the ordering, tie-breaking and arithmetic) and on real drafts
produced by :func:`releasenote.service.build_draft` (to prove it reflects the
same flags as the draft payload).
"""

import csv
import io
import json
import unittest

from releasenote.exporters import heatmap_csv, heatmap_json
from releasenote.heatmap import (
    HEAT_COOL,
    HEAT_HOT,
    HEAT_WARM,
    compute_heatmap,
    heat_for,
)
from releasenote.service import build_draft, build_heatmap, sample_heatmap

_CLEAN = (
    "id,type,title,description,benefit,source\n"
    "A,fix,Fix login,Login no longer fails,Reliable access,A\n"
)
_MIXED = (
    "id,type,title,description,benefit,source\n"
    "REL-1,feature,Bulk export,Export all reports,A much better experience,\n"
    "REL-2,fix,Timezone fix,Timestamps render locally,Correct local times,REL-2\n"
)


def _entry(entry_id, entry_type, title="Title"):
    return {
        "id": entry_id,
        "type": entry_type,
        "title": title,
        "description": "d",
        "benefit": "b",
        "source": "s",
    }


def _flag(code, severity, entry_id=""):
    return {
        "code": code,
        "severity": severity,
        "message": "m",
        "entry_id": entry_id,
    }


def _payload(entries, flags, band="review"):
    return {
        "entries": entries,
        "qa": {"flags": flags},
        "score": {"band": band},
    }


class HeatBandTests(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(heat_for(0), HEAT_COOL)
        for value in (1, 2, 3):
            self.assertEqual(heat_for(value), HEAT_WARM)
        for value in (4, 5, 20):
            self.assertEqual(heat_for(value), HEAT_HOT)

    def test_non_integer_is_cool(self):
        self.assertEqual(heat_for(None), HEAT_COOL)
        self.assertEqual(heat_for("nope"), HEAT_COOL)


class WeightedRiskTests(unittest.TestCase):
    def test_weighted_risk_arithmetic(self):
        payload = _payload(
            [_entry("A", "feature"), _entry("B", "fix")],
            [
                _flag("c1", "high", "A"),
                _flag("c2", "medium", "A"),
                _flag("c3", "low", "A"),
                _flag("c4", "low", "B"),
            ],
        )
        heatmap = compute_heatmap(payload)
        for row in heatmap["sections"] + heatmap["entries"] + heatmap["codes"]:
            self.assertEqual(
                row["weighted_risk"],
                3 * row["high"] + 2 * row["medium"] + 1 * row["low"],
                row,
            )

    def test_total_and_section_sum_agree(self):
        payload = _payload(
            [_entry("A", "feature"), _entry("B", "breaking")],
            [
                _flag("c1", "high", "A"),
                _flag("c2", "medium", "B"),
            ],
        )
        heatmap = compute_heatmap(payload)
        section_total = sum(s["weighted_risk"] for s in heatmap["sections"])
        code_total = sum(c["weighted_risk"] for c in heatmap["codes"])
        self.assertEqual(heatmap["total_weighted_risk"], section_total)
        self.assertEqual(heatmap["total_weighted_risk"], code_total)
        self.assertEqual(heatmap["total_weighted_risk"], 3 + 2)

    def test_section_bands(self):
        payload = _payload(
            [_entry("A", "feature"), _entry("B", "fix"), _entry("C", "other")],
            [_flag("c1", "high", "A")],
        )
        sections = {s["key"]: s for s in compute_heatmap(payload)["sections"]}
        self.assertEqual(sections["feature"]["heat"], HEAT_WARM)
        self.assertEqual(sections["feature"]["weighted_risk"], 3)
        self.assertEqual(sections["fix"]["heat"], HEAT_COOL)
        self.assertEqual(sections["other"]["heat"], HEAT_COOL)
        self.assertEqual(sections["breaking"]["heat"], HEAT_COOL)


class OrderingTests(unittest.TestCase):
    def test_entries_ordered_by_descending_risk_then_id(self):
        payload = _payload(
            [_entry("b", "other"), _entry("a", "other"), _entry("c", "fix")],
            [_flag("c1", "high", "c")],
        )
        entries = compute_heatmap(payload)["entries"]
        self.assertEqual([e["entry_id"] for e in entries], ["c", "a", "b"])
        self.assertEqual(entries[0]["weighted_risk"], 3)
        self.assertEqual(entries[1]["weighted_risk"], 0)
        self.assertEqual(entries[2]["weighted_risk"], 0)

    def test_codes_ordered_by_descending_risk_then_code(self):
        payload = _payload(
            [_entry("A", "feature")],
            [
                _flag("zeta", "low", "A"),
                _flag("alpha", "low", "A"),
                _flag("mid", "high", "A"),
            ],
        )
        codes = compute_heatmap(payload)["codes"]
        self.assertEqual([c["code"] for c in codes], ["mid", "alpha", "zeta"])
        self.assertEqual(codes[0]["weighted_risk"], 3)
        self.assertEqual(codes[1]["weighted_risk"], 1)
        self.assertEqual(codes[2]["weighted_risk"], 1)

    def test_hottest_section_ties_break_on_canonical_order(self):
        payload = _payload(
            [_entry("A", "feature"), _entry("B", "fix")],
            [_flag("c1", "high", "A"), _flag("c2", "high", "B")],
        )
        heatmap = compute_heatmap(payload)
        self.assertEqual(heatmap["hottest_section"], "feature")

    def test_hottest_section_empty_when_clean(self):
        heatmap = compute_heatmap(_payload([_entry("A", "fix")], []))
        self.assertEqual(heatmap["hottest_section"], "")
        self.assertEqual(heatmap["total_weighted_risk"], 0)


class RecommendationTests(unittest.TestCase):
    def test_hold_when_high_flag_exists(self):
        payload = _payload([_entry("A", "fix")], [_flag("c1", "high", "A")])
        recommendation = compute_heatmap(payload)["recommendation"]
        self.assertEqual(recommendation["decision"], "hold")
        self.assertIn("high-severity", recommendation["reason"])

    def test_hold_when_band_is_risky(self):
        payload = _payload(
            [_entry("A", "fix")],
            [_flag("c1", "medium", "A")],
            band="risky",
        )
        recommendation = compute_heatmap(payload)["recommendation"]
        self.assertEqual(recommendation["decision"], "hold")
        self.assertIn("risky", recommendation["reason"])

    def test_ship_when_no_high_and_not_risky(self):
        payload = _payload(
            [_entry("A", "fix")],
            [_flag("c1", "medium", "A")],
            band="review",
        )
        recommendation = compute_heatmap(payload)["recommendation"]
        self.assertEqual(recommendation["decision"], "ship")
        self.assertIn("review", recommendation["reason"])

    def test_deterministic(self):
        payload = _payload(
            [_entry("A", "feature")],
            [_flag("c1", "high", "A"), _flag("c2", "low", "A")],
        )
        self.assertEqual(compute_heatmap(payload), compute_heatmap(payload))


class ConfigTests(unittest.TestCase):
    def test_severity_override_changes_weight(self):
        payload = _payload(
            [_entry("A", "fix")], [_flag("vague_language", "medium", "A")]
        )
        base = compute_heatmap(payload)
        self.assertEqual(base["total_weighted_risk"], 2)
        overridden = compute_heatmap(
            payload, {"severity_overrides": {"vague_language": "high"}}
        )
        self.assertEqual(overridden["total_weighted_risk"], 3)
        self.assertEqual(overridden["codes"][0]["severity"], "high")

    def test_draft_level_flag_falls_under_other(self):
        payload = _payload(
            [_entry("A", "feature")],
            [_flag("missing_required_section", "medium", "")],
        )
        sections = {s["key"]: s for s in compute_heatmap(payload)["sections"]}
        self.assertEqual(sections["other"]["weighted_risk"], 2)
        self.assertEqual(sections["feature"]["weighted_risk"], 0)


class MalformedPayloadTests(unittest.TestCase):
    def test_rejects_non_object(self):
        with self.assertRaises(ValueError):
            compute_heatmap(None)
        with self.assertRaises(ValueError):
            compute_heatmap(["not", "an", "object"])

    def test_rejects_missing_entries(self):
        with self.assertRaises(ValueError):
            compute_heatmap({"qa": {"flags": []}})

    def test_rejects_missing_qa(self):
        with self.assertRaises(ValueError):
            compute_heatmap({"entries": []})

    def test_rejects_non_list_flags(self):
        with self.assertRaises(ValueError):
            compute_heatmap({"entries": [], "qa": {"flags": {}}})

    def test_rejects_non_object_entry(self):
        with self.assertRaises(ValueError):
            compute_heatmap({"entries": [1], "qa": {"flags": []}})

    def test_rejects_invalid_config(self):
        with self.assertRaises(ValueError):
            compute_heatmap(
                {"entries": [], "qa": {"flags": [], "publishable": True}},
                {"bogus": 1},
            )


class BuildHeatmapTests(unittest.TestCase):
    def test_build_heatmap_reflects_draft_flags(self):
        draft = build_draft(_MIXED, "csv")
        expected = compute_heatmap(draft)
        actual = build_heatmap({"content": _MIXED, "format": "csv"})
        for key in ("sections", "entries", "codes", "total_weighted_risk",
                    "hottest_section", "recommendation"):
            self.assertEqual(actual[key], expected[key], key)

    def test_build_heatmap_echoes_profile(self):
        result = build_heatmap(
            {"content": _MIXED, "format": "csv", "profile": "strict"}
        )
        self.assertEqual(result["profile"], "strict")

    def test_build_heatmap_clean_draft_ships(self):
        result = build_heatmap({"content": _CLEAN, "format": "csv"})
        self.assertEqual(result["recommendation"]["decision"], "ship")
        self.assertEqual(result["total_weighted_risk"], 0)

    def test_build_heatmap_requires_content(self):
        with self.assertRaises(ValueError):
            build_heatmap({"content": "   "})

    def test_build_heatmap_deterministic(self):
        first = build_heatmap(sample_heatmap())
        second = build_heatmap(sample_heatmap())
        self.assertEqual(first, second)


class HeatmapExportTests(unittest.TestCase):
    def test_csv_header_and_rows(self):
        heatmap = compute_heatmap(
            _payload(
                [_entry("A", "feature")],
                [_flag("c1", "high", "A")],
            )
        )
        output = heatmap_csv(heatmap)
        self.assertTrue(output.endswith("\n"))
        self.assertNotIn("\r", output)
        rows = list(csv.reader(io.StringIO(output)))
        self.assertEqual(
            rows[0],
            ["scope", "key", "label", "flag_count", "high", "medium", "low",
             "weighted_risk", "heat"],
        )
        scopes = [row[0] for row in rows[1:]]
        self.assertEqual(scopes, ["section"] * 4 + ["entry", "code"])

    def test_csv_quotes_special_fields(self):
        heatmap = compute_heatmap(
            _payload(
                [_entry("A", "feature", title='Comma, "quote" and\nnewline')],
                [],
            )
        )
        output = heatmap_csv(heatmap)
        rows = list(csv.reader(io.StringIO(output)))
        titles = [row[2] for row in rows if row[0] == "entry"]
        self.assertEqual(titles, ['Comma, "quote" and\nnewline'])

    def test_json_round_trips(self):
        heatmap = build_heatmap(sample_heatmap())
        text = heatmap_json(heatmap)
        self.assertEqual(json.loads(text), heatmap)

    def test_csv_without_heatmap_is_header_only(self):
        self.assertEqual(
            heatmap_csv({}),
            "scope,key,label,flag_count,high,medium,low,weighted_risk,heat\n",
        )


if __name__ == "__main__":
    unittest.main()
