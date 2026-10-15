import csv
import io
import json
import os
import tempfile
import unittest

from releasenote.archive import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_TAG_LENGTH,
    filter_drafts,
    normalize_tag,
    validate_tag,
)
from releasenote.exporters import ARCHIVE_HEADER, archive_csv, archive_json
from releasenote.service import (
    HistoryRequiredError,
    build_archive,
    get_archive_entry,
    tag_draft,
)
from releasenote.storage import ArchiveStore, DraftStore, ReviewStore


def _record(draft_id, created_at, **overrides):
    record = {
        "draft_id": draft_id,
        "title": "Release " + draft_id,
        "created_at": created_at,
        "status": "pending",
        "band": "ready",
        "score": 100,
        "entry_count": 1,
        "tags": [],
        "entry_titles": [],
    }
    record.update(overrides)
    return record


class TagValidationTests(unittest.TestCase):
    def test_normalize_trims_and_lowercases(self):
        self.assertEqual(normalize_tag("  Launch "), "launch")

    def test_normalize_rejects_non_string(self):
        with self.assertRaises(ValueError):
            normalize_tag(3)

    def test_validate_accepts_slug(self):
        self.assertEqual(validate_tag("2-0-launch_patch"), "2-0-launch_patch")
        self.assertEqual(validate_tag("SECURITY"), "security")

    def test_validate_rejects_empty(self):
        with self.assertRaises(ValueError):
            validate_tag("   ")

    def test_validate_rejects_too_long(self):
        with self.assertRaises(ValueError):
            validate_tag("a" * (MAX_TAG_LENGTH + 1))

    def test_validate_rejects_illegal_characters(self):
        for bad in ("bad tag", "tag!", "-leading", "a/b", "café"):
            with self.assertRaises(ValueError):
                validate_tag(bad)


class FilterDraftsTests(unittest.TestCase):
    def _records(self):
        return [
            _record(
                "b",
                "2026-01-02T00:00:00+00:00",
                title="2.0 launch",
                status="approved",
                band="ready",
                tags=["launch", "2.0"],
                entry_titles=["Bulk export"],
            ),
            _record(
                "a",
                "2026-01-02T00:00:00+00:00",
                title="Security patch",
                status="pending",
                band="review",
                tags=["security"],
                entry_titles=["Fix login"],
            ),
            _record(
                "c",
                "2026-01-03T00:00:00+00:00",
                title="Hotfix",
                status="rejected",
                band="risky",
                tags=["security"],
                entry_titles=["Crash fix"],
            ),
        ]

    def test_order_created_at_desc_then_draft_id_asc(self):
        report = filter_drafts(self._records())
        self.assertEqual([d["draft_id"] for d in report["drafts"]], ["c", "a", "b"])
        self.assertEqual(report["total"], 3)

    def test_query_matches_title_case_insensitively(self):
        report = filter_drafts(self._records(), query="LAUNCH")
        self.assertEqual([d["draft_id"] for d in report["drafts"]], ["b"])

    def test_query_matches_entry_title(self):
        report = filter_drafts(self._records(), query="fix login")
        self.assertEqual([d["draft_id"] for d in report["drafts"]], ["a"])

    def test_tag_filter(self):
        report = filter_drafts(self._records(), tag="SECURITY")
        self.assertEqual([d["draft_id"] for d in report["drafts"]], ["c", "a"])

    def test_status_filter(self):
        report = filter_drafts(self._records(), status="approved")
        self.assertEqual([d["draft_id"] for d in report["drafts"]], ["b"])

    def test_band_filter(self):
        report = filter_drafts(self._records(), band="risky")
        self.assertEqual([d["draft_id"] for d in report["drafts"]], ["c"])

    def test_since_until_inclusive(self):
        report = filter_drafts(
            self._records(),
            since="2026-01-02T00:00:00+00:00",
            until="2026-01-02T00:00:00+00:00",
        )
        self.assertEqual([d["draft_id"] for d in report["drafts"]], ["a", "b"])

    def test_limit_and_offset_do_not_drop_or_duplicate(self):
        all_ids = [d["draft_id"] for d in filter_drafts(self._records())["drafts"]]
        page1 = filter_drafts(self._records(), limit=2, offset=0)["drafts"]
        page2 = filter_drafts(self._records(), limit=2, offset=2)["drafts"]
        self.assertEqual(len(page1), 2)
        self.assertEqual(len(page2), 1)
        paged_ids = [d["draft_id"] for d in page1 + page2]
        self.assertEqual(paged_ids, all_ids)
        self.assertEqual(len(set(paged_ids)), len(paged_ids))

    def test_limit_defaults_and_caps(self):
        self.assertEqual(filter_drafts([])["limit"], DEFAULT_LIMIT)
        self.assertEqual(filter_drafts([], limit=9999)["limit"], MAX_LIMIT)

    def test_entry_titles_stripped_from_result(self):
        report = filter_drafts(self._records())
        self.assertNotIn("entry_titles", report["drafts"][0])

    def test_invalid_status_raises(self):
        with self.assertRaises(ValueError):
            filter_drafts(self._records(), status="nope")

    def test_invalid_band_raises(self):
        with self.assertRaises(ValueError):
            filter_drafts(self._records(), band="nope")

    def test_invalid_tag_raises(self):
        with self.assertRaises(ValueError):
            filter_drafts(self._records(), tag="bad tag")

    def test_invalid_since_raises(self):
        with self.assertRaises(ValueError):
            filter_drafts(self._records(), since="not-a-date")

    def test_invalid_limit_raises(self):
        with self.assertRaises(ValueError):
            filter_drafts(self._records(), limit="many")


class ArchiveExportTests(unittest.TestCase):
    def _report(self):
        return {
            "drafts": [
                {
                    "draft_id": "b",
                    "title": 'Launch, "2.0"',
                    "created_at": "2026-01-02T00:00:00+00:00",
                    "status": "approved",
                    "band": "ready",
                    "entry_count": 3,
                    "tags": ["launch", "2.0"],
                }
            ],
            "total": 1,
            "limit": 50,
            "offset": 0,
        }

    def test_csv_header_and_tags_joined(self):
        rows = list(csv.reader(io.StringIO(archive_csv(self._report()))))
        self.assertEqual(list(ARCHIVE_HEADER), rows[0])
        self.assertEqual(rows[1][0], "b")
        self.assertEqual(rows[1][1], 'Launch, "2.0"')
        self.assertEqual(rows[1][6], "launch|2.0")

    def test_csv_line_endings(self):
        text = archive_csv(self._report())
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("\r", text)

    def test_csv_empty_report_is_header_only(self):
        self.assertEqual(archive_csv({}), ",".join(ARCHIVE_HEADER) + "\n")

    def test_json_is_stable_and_has_no_nan(self):
        text = archive_json(self._report())
        self.assertNotIn("NaN", text)
        self.assertEqual(json.loads(text), self._report())
        self.assertTrue(text.startswith('{"drafts"'))


class ArchiveStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ArchiveStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_is_durable_with_data_dir(self):
        self.assertTrue(self.store.is_durable)
        self.assertFalse(ArchiveStore().is_durable)

    def test_add_and_current_tags(self):
        self.store.tag("d1", "Launch", "add")
        self.store.tag("d1", "v2", "add")
        self.assertEqual(self.store.tags_for("d1"), ["launch", "v2"])

    def test_latest_wins_on_remove(self):
        self.store.tag("d1", "launch", "add")
        self.store.tag("d1", "launch", "remove")
        self.assertEqual(self.store.tags_for("d1"), [])

    def test_repeated_add_is_idempotent(self):
        self.store.tag("d1", "launch", "add")
        self.store.tag("d1", "launch", "add")
        self.assertEqual(self.store.tags_for("d1"), ["launch"])
        self.assertEqual(len(self.store.audit("d1")), 2)

    def test_seq_is_strictly_increasing(self):
        first = self.store.tag("d1", "launch", "add")
        second = self.store.tag("d1", "security", "add")
        self.assertLess(first["seq"], second["seq"])

    def test_audit_is_ordered(self):
        self.store.tag("d1", "launch", "add")
        self.store.tag("d1", "launch", "remove")
        actions = [entry["action"] for entry in self.store.audit("d1")]
        self.assertEqual(actions, ["add", "remove"])

    def test_invalid_action_appends_nothing(self):
        with self.assertRaises(ValueError):
            self.store.tag("d1", "launch", "flip")
        self.assertEqual(self.store.audit("d1"), [])

    def test_invalid_tag_appends_nothing(self):
        with self.assertRaises(ValueError):
            self.store.tag("d1", "bad tag", "add")
        self.assertEqual(self.store.audit("d1"), [])

    def test_log_is_append_only_and_persists(self):
        record = self.store.tag("d1", "launch", "add")
        with open(os.path.join(self.tmp.name, "archive.jsonl"), encoding="utf-8") as fh:
            lines = [line for line in fh if line.strip()]
        self.assertEqual(len(lines), 1)
        other = ArchiveStore(self.tmp.name)
        self.assertEqual(other.tags_for("d1"), ["launch"])
        self.assertEqual(other.audit("d1")[0]["seq"], record["seq"])

    def test_in_memory_fallback(self):
        store = ArchiveStore()
        store.tag("d1", "launch", "add")
        self.assertEqual(store.tags_for("d1"), ["launch"])


class ArchiveServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.drafts = DraftStore(self.tmp.name)
        self.reviews = ReviewStore(self.tmp.name, self.drafts)
        self.archive = ArchiveStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _save(self, title, entries=None):
        return self.drafts.save(
            title,
            "# " + title,
            len(entries or []),
            0,
            entries=entries or [],
            config={},
        )

    def test_build_archive_requires_history(self):
        with self.assertRaises(HistoryRequiredError):
            build_archive()

    def test_tag_draft_requires_history(self):
        with self.assertRaises(HistoryRequiredError):
            tag_draft("d1", "launch", "add")

    def test_get_archive_entry_requires_history(self):
        with self.assertRaises(HistoryRequiredError):
            get_archive_entry("d1")

    def test_build_archive_lists_and_tags(self):
        record = self._save("Release 1")
        self.archive.tag(record["id"], "launch", "add")
        report = build_archive(
            draft_store=self.drafts,
            review_store=self.reviews,
            archive_store=self.archive,
        )
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["drafts"][0]["tags"], ["launch"])
        self.assertIn(report["drafts"][0]["band"], ("ready", "review", "risky"))

    def test_tag_draft_unknown_is_lookup_error(self):
        with self.assertRaises(LookupError):
            tag_draft(
                "missing",
                "launch",
                "add",
                self.drafts,
                self.reviews,
                self.archive,
            )

    def test_tag_draft_bad_tag_appends_nothing(self):
        record = self._save("Release 1")
        with self.assertRaises(ValueError):
            tag_draft(
                record["id"],
                "",
                "add",
                self.drafts,
                self.reviews,
                self.archive,
            )
        self.assertEqual(self.archive.audit(record["id"]), [])

    def test_tag_draft_bad_action_appends_nothing(self):
        record = self._save("Release 1")
        with self.assertRaises(ValueError):
            tag_draft(
                record["id"],
                "launch",
                "flip",
                self.drafts,
                self.reviews,
                self.archive,
            )
        self.assertEqual(self.archive.audit(record["id"]), [])

    def test_get_archive_entry_includes_audit(self):
        record = self._save("Release 1")
        tag_draft(
            record["id"],
            "launch",
            "add",
            self.drafts,
            self.reviews,
            self.archive,
        )
        entry = get_archive_entry(
            record["id"], self.drafts, self.reviews, self.archive
        )
        self.assertEqual(entry["tags"], ["launch"])
        self.assertEqual(len(entry["audit"]), 1)
        self.assertEqual(entry["audit"][0]["action"], "add")


if __name__ == "__main__":
    unittest.main()
