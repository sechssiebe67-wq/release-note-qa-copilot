import json
import unittest

from releasenote.config import WorkspaceConfig
from releasenote.diff import compute_diff, diff_drafts
from releasenote.importer import parse_entries
from releasenote.models import ChangeEntry


def entries(csv, fmt="csv"):
    return parse_entries(csv, fmt)


PREVIOUS = (
    "id,type,title,description,benefit,source,link\n"
    "A,feature,Alpha,Alpha does things,Fast and reliable,SRC-A,https://a\n"
    "B,feature,Beta,Beta description,Unique beta value,SRC-B,\n"
    "C,fix,Gamma,Gamma description,Gamma value,SRC-C,\n"
)


def _ids(items):
    return {item["id"] for item in items}


class ClassificationTests(unittest.TestCase):
    def test_added_entry_detected(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Fast and reliable,SRC-A,https://a\n"
            "B,feature,Beta,Beta description,Unique beta value,SRC-B,\n"
            "C,fix,Gamma,Gamma description,Gamma value,SRC-C,\n"
            "D,feature,Delta,A brand new thing,Delta value,SRC-D,\n"
        )
        diff = diff_drafts(entries(PREVIOUS), entries(current))
        self.assertEqual(_ids(diff["added"]), {"D"})
        self.assertIn("reason", diff["added"][0])

    def test_removed_entry_detected(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Fast and reliable,SRC-A,https://a\n"
            "C,fix,Gamma,Gamma description,Gamma value,SRC-C,\n"
        )
        diff = diff_drafts(entries(PREVIOUS), entries(current))
        self.assertEqual(_ids(diff["removed"]), {"B"})
        self.assertEqual(_ids(diff["unchanged"]), {"A", "C"})

    def test_unchanged_entry(self):
        diff = diff_drafts(entries(PREVIOUS), entries(PREVIOUS))
        self.assertEqual(diff["added"], [])
        self.assertEqual(diff["removed"], [])
        self.assertEqual(diff["changed"], [])
        self.assertEqual(_ids(diff["unchanged"]), {"A", "B", "C"})

    def test_changed_entry_by_id(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things faster,Great speed,SRC-A,https://a\n"
            "B,feature,Beta,Beta description,Unique beta value,SRC-B,\n"
            "C,fix,Gamma,Gamma description,Gamma value,SRC-C,\n"
        )
        diff = diff_drafts(entries(PREVIOUS), entries(current))
        self.assertEqual(_ids(diff["changed"]), {"A"})
        reason = diff["changed"][0]["reason"]
        self.assertIn("description", reason)
        self.assertIn("benefit", reason)

    def test_matches_by_normalized_title_when_id_changes(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A1,feature,alpha,Alpha does things,Fast and reliable,SRC-A,https://a\n"
        )
        diff = diff_drafts(entries(PREVIOUS), entries(current))
        self.assertEqual(_ids(diff["unchanged"]), {"A1"})
        self.assertEqual(_ids(diff["removed"]), {"B", "C"})


class RepeatedClaimTests(unittest.TestCase):
    def test_reused_benefit_text_is_repeated_claim(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Unique beta value,SRC-A,https://a\n"
        )
        diff, flags = compute_diff(entries(PREVIOUS), entries(current))
        self.assertTrue(
            any(item["id"] == "A" for item in diff["repeated_claims"])
        )
        repeated = [f for f in flags if f.code == "repeated_claim"]
        self.assertEqual(len(repeated), 1)
        self.assertEqual(repeated[0].severity, "medium")
        self.assertIn("reused", repeated[0].message)

    def test_unchanged_title_and_description_is_repeated_claim(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Fresh new benefit,SRC-A,https://a\n"
        )
        diff, flags = compute_diff(entries(PREVIOUS), entries(current))
        self.assertTrue(
            any(item["id"] == "A" for item in diff["repeated_claims"])
        )
        self.assertTrue(any(f.code == "repeated_claim" for f in flags))


class RegressionTests(unittest.TestCase):
    def test_lost_source_is_unsourced_regression(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Reliable and fast,,\n"
            "B,feature,Beta,Beta description,Unique beta value,SRC-B,\n"
            "C,fix,Gamma,Gamma description,Gamma value,SRC-C,\n"
        )
        diff, flags = compute_diff(entries(PREVIOUS), entries(current))
        self.assertTrue(
            any(item["id"] == "A" for item in diff["regressions"])
        )
        unsourced = [f for f in flags if f.code == "regression_unsourced"]
        self.assertEqual(len(unsourced), 1)
        self.assertEqual(unsourced[0].severity, "high")
        self.assertEqual(unsourced[0].entry_id, "A")

    def test_benefit_becoming_vague_is_regression(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Seamless and robust,SRC-A,https://a\n"
            "B,feature,Beta,Beta description,Unique beta value,SRC-B,\n"
            "C,fix,Gamma,Gamma description,Gamma value,SRC-C,\n"
        )
        diff, flags = compute_diff(entries(PREVIOUS), entries(current))
        self.assertTrue(any(item["id"] == "A" for item in diff["regressions"]))
        vague = [f for f in flags if f.code == "regression_vague_benefit"]
        self.assertEqual(len(vague), 1)
        self.assertEqual(vague[0].severity, "medium")

    def test_previously_publishable_now_high(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,,Alpha does things,Reliable and fast,SRC-A,https://a\n"
            "B,feature,Beta,Beta description,Unique beta value,SRC-B,\n"
            "C,fix,Gamma,Gamma description,Gamma value,SRC-C,\n"
        )
        diff, _flags = compute_diff(entries(PREVIOUS), entries(current))
        regression = next(
            item for item in diff["regressions"] if item["id"] == "A"
        )
        self.assertIn("high-severity", regression["reason"])

    def test_no_regressions_for_improved_entry(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha now does more,Measurable 2x speed,SRC-A,https://a\n"
            "B,feature,Beta,Beta description,Unique beta value,SRC-B,\n"
            "C,fix,Gamma,Gamma description,Gamma value,SRC-C,\n"
        )
        diff = diff_drafts(entries(PREVIOUS), entries(current))
        self.assertEqual(diff["regressions"], [])


class ConfigInteractionTests(unittest.TestCase):
    def test_severity_override_changes_regression_flag(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Reliable and fast,,\n"
        )
        config = WorkspaceConfig.from_dict(
            {"severity_overrides": {"regression_unsourced": "low"}}
        )
        _diff, flags = compute_diff(entries(PREVIOUS), entries(current), config)
        unsourced = [f for f in flags if f.code == "regression_unsourced"]
        self.assertEqual(unsourced[0].severity, "low")

    def test_extra_vague_term_triggers_vague_benefit_regression(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Groundbreaking speed,SRC-A,https://a\n"
        )
        config = WorkspaceConfig.from_dict(
            {"extra_vague_terms": ["groundbreaking"]}
        )
        diff, flags = compute_diff(entries(PREVIOUS), entries(current), config)
        self.assertTrue(any(item["id"] == "A" for item in diff["regressions"]))
        self.assertTrue(any(f.code == "regression_vague_benefit" for f in flags))


class InputShapeTests(unittest.TestCase):
    def test_accepts_changeentry_lists(self):
        previous = [ChangeEntry(id="X", title="Thing", source="S")]
        current = [ChangeEntry(id="X", title="Thing", source="S")]
        diff = diff_drafts(previous, current)
        self.assertEqual(_ids(diff["unchanged"]), {"X"})

    def test_accepts_parsed_draft_payload(self):
        from releasenote.service import build_draft

        previous = build_draft(PREVIOUS, "csv")
        current = build_draft(PREVIOUS, "csv")
        diff = diff_drafts(previous, current)
        self.assertEqual(_ids(diff["unchanged"]), {"A", "B", "C"})

    def test_result_is_json_serialisable(self):
        diff = diff_drafts(entries(PREVIOUS), entries(PREVIOUS))
        self.assertEqual(
            set(diff),
            {
                "added",
                "changed",
                "removed",
                "unchanged",
                "repeated_claims",
                "regressions",
            },
        )
        json.dumps(diff)


if __name__ == "__main__":
    unittest.main()
