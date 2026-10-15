import json
import os
import tempfile
import unittest

from releasenote import service
from releasenote.storage import DraftVersionStore
from releasenote.versioning import (
    diff_versions,
    next_version,
    rollback_plan,
    sort_versions,
    validate_draft_key,
)


ENTRY_A = {
    "id": "A",
    "type": "fix",
    "title": "Fix login",
    "description": "Login no longer fails",
    "benefit": "Reliable access",
    "source": "A",
    "link": "",
    "audience": "",
}
ENTRY_B = {
    "id": "B",
    "type": "feature",
    "title": "Bulk export",
    "description": "Export every report",
    "benefit": "One-click export saves time",
    "source": "B",
    "link": "",
    "audience": "",
}


class ValidateDraftKeyTests(unittest.TestCase):
    def test_trims_and_returns_canonical(self):
        self.assertEqual(validate_draft_key("  release-2.0  "), "release-2.0")

    def test_accepts_dots_underscores_and_dashes(self):
        for key in ("v1.2.3", "security_patch", "rel-2026-01", "0"):
            self.assertEqual(validate_draft_key(key), key)

    def test_rejects_empty_and_non_string(self):
        for value in ("", "   ", None, 42, []):
            with self.assertRaises(ValueError):
                validate_draft_key(value)

    def test_rejects_uppercase_space_and_leading_separator(self):
        for value in ("Release", "release 2", "-release", ".release", "_x"):
            with self.assertRaises(ValueError):
                validate_draft_key(value)

    def test_rejects_overlong_key(self):
        with self.assertRaises(ValueError):
            validate_draft_key("a" * 65)
        self.assertEqual(validate_draft_key("a" * 64), "a" * 64)


class NextVersionTests(unittest.TestCase):
    def test_empty_is_one(self):
        self.assertEqual(next_version([]), 1)
        self.assertEqual(next_version(None), 1)

    def test_max_plus_one(self):
        self.assertEqual(next_version([1, 3, 2]), 4)
        self.assertEqual(next_version(["2", 5]), 6)


class SortVersionsTests(unittest.TestCase):
    def test_orders_by_version_ascending(self):
        records = [
            {"version": 2, "draft_id": "b"},
            {"version": 1, "draft_id": "a"},
        ]
        ordered = sort_versions(records)
        self.assertEqual([r["version"] for r in ordered], [1, 2])

    def test_stable_on_ties(self):
        records = [
            {"version": 1, "draft_id": "first"},
            {"version": 1, "draft_id": "second"},
        ]
        ordered = sort_versions(records)
        self.assertEqual([r["draft_id"] for r in ordered], ["first", "second"])

    def test_ignores_non_mappings(self):
        ordered = sort_versions([{"version": 1}, "nope", None])
        self.assertEqual(len(ordered), 1)


class DiffVersionsTests(unittest.TestCase):
    def test_uses_entries_and_reports_versions(self):
        previous = {"version": 1, "entries": [ENTRY_A]}
        current = {"version": 2, "entries": [ENTRY_A, ENTRY_B]}
        report = diff_versions(previous, current)
        self.assertEqual(report["previous_version"], 1)
        self.assertEqual(report["current_version"], 2)
        self.assertEqual(
            [item["id"] for item in report["diff"]["added"]], ["B"]
        )
        self.assertEqual(
            [item["id"] for item in report["diff"]["unchanged"]], ["A"]
        )
        for category in (
            "added",
            "changed",
            "removed",
            "unchanged",
            "repeated_claims",
            "regressions",
        ):
            self.assertIn(category, report["diff"])
        self.assertIsInstance(report["flags"], list)


class RollbackPlanTests(unittest.TestCase):
    def test_returns_target_record(self):
        records = [{"version": 1}, {"version": 2}]
        self.assertEqual(rollback_plan(records, 1), {"version": 1})

    def test_missing_version_raises(self):
        with self.assertRaises(ValueError):
            rollback_plan([{"version": 1}], 9)

    def test_non_integer_version_raises(self):
        with self.assertRaises(ValueError):
            rollback_plan([{"version": 1}], "abc")


class DraftVersionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = DraftVersionStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_assigns_versions_and_seq(self):
        first = self.store.append("release-2.0", "d1", {"entries": [ENTRY_A]})
        second = self.store.append("release-2.0", "d2", {"entries": []})
        self.assertEqual((first["version"], second["version"]), (1, 2))
        self.assertLess(first["seq"], second["seq"])
        for key in ("draft_key", "version", "draft_id", "created_at", "seq"):
            self.assertIn(key, first)

    def test_is_durable_and_persists_across_instances(self):
        self.assertTrue(self.store.is_durable)
        self.store.append("rel", "d1", {"entries": [ENTRY_A]})
        other = DraftVersionStore(self.tmp.name)
        versions = other.list_versions("rel")
        self.assertEqual([v["version"] for v in versions], [1])
        self.assertEqual(other.get_version("rel", 1)["draft_id"], "d1")
        self.assertEqual(other.latest("rel")["version"], 1)

    def test_unknown_key_and_version(self):
        self.assertEqual(self.store.list_versions("missing"), [])
        self.assertIsNone(self.store.get_version("missing", 1))
        self.assertIsNone(self.store.latest("missing"))

    def test_latest_line_per_version_wins(self):
        self.store.append("rel", "old", {"entries": []})
        with open(self.store.path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "seq": 99,
                        "draft_key": "rel",
                        "version": 1,
                        "draft_id": "new",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "payload": {"version": 1},
                    }
                )
                + "\n"
            )
        self.assertEqual(self.store.get_version("rel", 1)["draft_id"], "new")

    def test_in_memory_fallback(self):
        store = DraftVersionStore()
        self.assertFalse(store.is_durable)
        store.append("rel", "d1", {"entries": []})
        store.append("rel", "d2", {"entries": []})
        self.assertEqual(
            [v["version"] for v in store.list_versions("rel")], [1, 2]
        )
        self.assertEqual(store.latest("rel")["draft_id"], "d2")


class ServiceVersioningTests(unittest.TestCase):
    def setUp(self):
        self.store = DraftVersionStore()

    def _build(self, key, content=None):
        content = content or (
            "id,type,title,description,benefit,source\n"
            "A,fix,Fix login,Login no longer fails,Reliable access,A\n"
        )
        payload = service.build_draft(
            content, "csv", draft_key=key, version_store=self.store
        )
        return payload

    def test_build_draft_echoes_key_and_version(self):
        first = self._build("rel")
        self.assertEqual(first["draft_key"], "rel")
        self.assertEqual(first["version"], 1)
        service.store_draft_version("rel", "d1", first, self.store)
        second = self._build("rel")
        self.assertEqual(second["version"], 2)

    def test_list_get_diff_and_rollback(self):
        first = self._build("rel")
        service.store_draft_version("rel", "d1", first, self.store)
        second = self._build("rel")
        service.store_draft_version("rel", "d2", second, self.store)

        listing = service.list_draft_versions("rel", self.store)
        self.assertEqual([v["version"] for v in listing["versions"]], [1, 2])
        self.assertEqual(listing["draft_key"], "rel")

        payload = service.get_draft_version("rel", 1, self.store)
        self.assertEqual(payload["version"], 1)

        report = service.diff_draft_versions("rel", 1, 2, None, self.store)
        self.assertEqual(report["previous_version"], 1)
        self.assertEqual(report["current_version"], 2)

        rolled = service.rollback_draft_version("rel", 1, self.store)
        self.assertEqual(rolled["version"], 1)
        self.assertEqual(rolled["rolled_back_from"], 2)

    def test_unknown_version_and_key(self):
        self._build("rel")
        with self.assertRaises(LookupError):
            service.get_draft_version("rel", 9, self.store)
        with self.assertRaises(LookupError):
            service.rollback_draft_version("rel", 9, self.store)
        self.assertEqual(
            service.list_draft_versions("other", self.store)["versions"], []
        )

    def test_missing_store_raises_history_required(self):
        for call in (
            lambda: service.list_draft_versions("rel"),
            lambda: service.get_draft_version("rel", 1),
            lambda: service.diff_draft_versions("rel", 1, 2),
            lambda: service.rollback_draft_version("rel", 1),
        ):
            with self.assertRaises(service.HistoryRequiredError):
                call()


if __name__ == "__main__":
    unittest.main()
