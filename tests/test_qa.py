import unittest

from releasenote.config import WorkspaceConfig
from releasenote.models import (
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    TYPE_BREAKING,
    TYPE_FEATURE,
    TYPE_FIX,
    ChangeEntry,
)
from releasenote.qa import analyze_entries, analyze_entry, checklist


def codes(flags):
    return {f.code for f in flags}


def flag_by_code(flags, code):
    return next(f for f in flags if f.code == code)


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


class WorkspaceConfigQATests(unittest.TestCase):
    def test_extra_vague_term_flagged(self):
        entry = ChangeEntry(
            id="A",
            type=TYPE_FIX,
            title="Blazing sync",
            description="Sync is blazing fast.",
            source="A",
        )
        default_flags = analyze_entry(entry)
        self.assertNotIn("vague_language", codes(default_flags))

        config = WorkspaceConfig.from_dict({"extra_vague_terms": ["blazing"]})
        flags = analyze_entry(entry, config)
        self.assertIn("vague_language", codes(flags))

    def test_allow_term_removes_builtin_vague(self):
        entry = ChangeEntry(
            id="A",
            type=TYPE_FIX,
            title="Seamless sync",
            description="Sync is seamless now.",
            source="A",
        )
        self.assertIn("vague_language", codes(analyze_entry(entry)))

        config = WorkspaceConfig.from_dict({"allow_terms": ["Seamless"]})
        self.assertNotIn("vague_language", codes(analyze_entry(entry, config)))

    def test_banned_phrase_flagged_with_phrase(self):
        entry = ChangeEntry(
            id="A",
            type=TYPE_FIX,
            title="Sync",
            description="The world's best sync, guaranteed.",
            source="A",
        )
        config = WorkspaceConfig.from_dict(
            {"banned_phrases": ["world's best", "guaranteed"]}
        )
        flags = analyze_entry(entry, config)
        banned = [f for f in flags if f.code == "banned_phrase"]
        self.assertEqual(len(banned), 2)
        self.assertTrue(all(f.severity == SEVERITY_MEDIUM for f in banned))
        messages = " ".join(f.message for f in banned)
        self.assertIn("world's best", messages)
        self.assertIn("guaranteed", messages)

    def test_banned_phrase_case_insensitive(self):
        entry = ChangeEntry(
            id="A", type=TYPE_FIX, title="Sync", description="Guaranteed!", source="A"
        )
        config = WorkspaceConfig.from_dict({"banned_phrases": ["GUARANTEED"]})
        self.assertIn("banned_phrase", codes(analyze_entry(entry, config)))

    def test_severity_override_changes_severity(self):
        entry = ChangeEntry(
            id="A",
            type=TYPE_FIX,
            title="Better sync",
            description="Sync is better.",
            source="A",
        )
        config = WorkspaceConfig.from_dict(
            {"severity_overrides": {"vague_language": "high"}}
        )
        flag = flag_by_code(analyze_entry(entry, config), "vague_language")
        self.assertEqual(flag.severity, SEVERITY_HIGH)

    def test_severity_override_can_lower(self):
        entry = ChangeEntry(id="A", type=TYPE_FIX, title="T", description="D")
        config = WorkspaceConfig.from_dict(
            {"severity_overrides": {"missing_source": "low"}}
        )
        flag = flag_by_code(analyze_entry(entry, config), "missing_source")
        self.assertEqual(flag.severity, SEVERITY_LOW)

    def test_default_config_keeps_builtin_severity(self):
        entry = ChangeEntry(
            id="A",
            type=TYPE_FIX,
            title="Better sync",
            description="Sync is better.",
            source="A",
        )
        flag = flag_by_code(
            analyze_entry(entry, WorkspaceConfig.default()), "vague_language"
        )
        self.assertEqual(flag.severity, SEVERITY_MEDIUM)

    def test_required_section_missing_flag(self):
        entries = [
            ChangeEntry(
                id="1", type=TYPE_FIX, title="T", description="D", source="1"
            )
        ]
        config = WorkspaceConfig.from_dict({"required_sections": ["breaking"]})
        qa = analyze_entries(entries, config)
        flag = flag_by_code(qa.flags, "missing_required_section")
        self.assertEqual(flag.severity, SEVERITY_MEDIUM)
        self.assertEqual(flag.entry_id, "")
        self.assertIn("breaking", flag.message)

    def test_required_section_present_no_flag(self):
        entries = [
            ChangeEntry(
                id="1",
                type=TYPE_BREAKING,
                title="T",
                description="Migrate now.",
                source="1",
            )
        ]
        config = WorkspaceConfig.from_dict({"required_sections": ["breaking"]})
        qa = analyze_entries(entries, config)
        self.assertNotIn("missing_required_section", codes(qa.flags))

    def test_required_section_severity_override(self):
        config = WorkspaceConfig.from_dict(
            {
                "required_sections": ["feature"],
                "severity_overrides": {"missing_required_section": "high"},
            }
        )
        qa = analyze_entries([], config)
        flag = flag_by_code(qa.flags, "missing_required_section")
        self.assertEqual(flag.severity, SEVERITY_HIGH)


class AnalyzeEntriesTests(unittest.TestCase):
    def test_aggregates_and_publishability(self):
        good = ChangeEntry(
            id="1", type=TYPE_FIX, title="Alpha", description="D", source="1"
        )
        bad = ChangeEntry(id="2", type=TYPE_FIX, title="Beta", description="D")
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
