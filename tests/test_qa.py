import unittest

from releasenote.models import (
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    TYPE_BREAKING,
    TYPE_FEATURE,
    TYPE_FIX,
    ChangeEntry,
)
from releasenote.qa import analyze_entries, analyze_entry, checklist


def codes(flags):
    return {f.code for f in flags}


class AnalyzeEntryTests(unittest.TestCase):
    def test_clean_entry_has_no_flags(self):
        entry = ChangeEntry(
            id="REL-1",
            type=TYPE_FIX,
            title="Fix timezone rendering",
            description="Timestamps now use the user timezone.",
            benefit="Correct dates everywhere.",
            source="REL-1",
        )
        self.assertEqual(analyze_entry(entry), [])

    def test_missing_source_is_high(self):
        entry = ChangeEntry(id="A", type=TYPE_FIX, title="T", description="D")
        flags = analyze_entry(entry)
        self.assertIn("missing_source", codes(flags))
        self.assertIn(SEVERITY_HIGH, {f.severity for f in flags})

    def test_vague_language_flagged(self):
        entry = ChangeEntry(
            id="A",
            type=TYPE_FEATURE,
            title="Better dashboard",
            description="The dashboard is improved.",
            benefit="A much better experience.",
            source="A",
        )
        flags = analyze_entry(entry)
        self.assertIn("vague_language", codes(flags))

    def test_feature_without_benefit(self):
        entry = ChangeEntry(
            id="A", type=TYPE_FEATURE, title="New thing", description="D", source="A"
        )
        self.assertIn("missing_benefit", codes(analyze_entry(entry)))

    def test_quantified_claim_without_source(self):
        entry = ChangeEntry(
            id="A",
            type=TYPE_FIX,
            title="Faster sync",
            description="Sync is 50% faster.",
        )
        flags = analyze_entry(entry)
        self.assertIn("quantified_claim_without_source", codes(flags))

    def test_quantified_claim_with_source_ok(self):
        entry = ChangeEntry(
            id="A",
            type=TYPE_FIX,
            title="Faster sync",
            description="Sync is 50% faster.",
            source="A",
        )
        self.assertNotIn(
            "quantified_claim_without_source", codes(analyze_entry(entry))
        )

    def test_breaking_without_migration(self):
        entry = ChangeEntry(
            id="A",
            type=TYPE_BREAKING,
            title="Config change",
            description="The config format changed.",
            source="A",
        )
        self.assertIn(
            "breaking_without_migration", codes(analyze_entry(entry))
        )

    def test_breaking_with_migration_ok(self):
        entry = ChangeEntry(
            id="A",
            type=TYPE_BREAKING,
            title="Config change",
            description="Migrate config.ini to config.yml before upgrading.",
            source="A",
        )
        self.assertNotIn(
            "breaking_without_migration", codes(analyze_entry(entry))
        )

    def test_missing_title_and_description(self):
        entry = ChangeEntry(id="A", type=TYPE_FIX)
        flags = analyze_entry(entry)
        self.assertIn("missing_title", codes(flags))
        self.assertIn("missing_description", codes(flags))


class AnalyzeEntriesTests(unittest.TestCase):
    def test_aggregates_and_publishability(self):
        good = ChangeEntry(
            id="1", type=TYPE_FIX, title="T", description="D", source="1"
        )
        bad = ChangeEntry(id="2", type=TYPE_FIX, title="T", description="D")
        qa = analyze_entries([good, bad])
        self.assertFalse(qa.publishable)
        self.assertEqual(len(qa.high), 1)
        self.assertEqual(len(qa.flags_for("2")), 1)
        self.assertEqual(qa.flags_for("1"), [])

    def test_empty_entries_publishable(self):
        qa = analyze_entries([])
        self.assertTrue(qa.publishable)
        self.assertTrue(qa.medium == [])


class ChecklistTests(unittest.TestCase):
    def test_flagged_checks(self):
        entries = [
            ChangeEntry(id="1", type=TYPE_FIX, title="T", description="D"),
            ChangeEntry(
                id="2",
                type=TYPE_BREAKING,
                title="T",
                description="changed",
                source="2",
            ),
        ]
        qa = analyze_entries(entries)
        items = checklist(entries, qa)
        statuses = {item["label"]: item["status"] for item in items}
        self.assertEqual(statuses["Every entry has a source or link"], "fail")
        self.assertEqual(
            statuses["Breaking changes describe the migration"], "fail"
        )
        self.assertEqual(statuses["Draft is publishable (no high severity flags)"], "fail")

    def test_all_good(self):
        entries = [
            ChangeEntry(
                id="1", type=TYPE_FIX, title="T", description="D", source="1"
            )
        ]
        qa = analyze_entries(entries)
        items = checklist(entries, qa)
        self.assertTrue(all(item["status"] == "pass" for item in items))

    def test_empty_entries_fail(self):
        items = checklist([], analyze_entries([]))
        self.assertTrue(all(item["status"] == "fail" for item in items))


if __name__ == "__main__":
    unittest.main()
