import os
import tempfile
import unittest

from releasenote.storage import CommentStore, DraftStore


class CommentStoreDurableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.drafts = DraftStore(self.tmp.name)
        record = self.drafts.save("R1", "# R1", 1, 0)
        self.draft_id = record["id"]
        self.store = CommentStore(self.tmp.name, self.drafts)

    def tearDown(self):
        self.tmp.cleanup()

    def test_is_durable(self):
        self.assertTrue(self.store.is_durable)

    def test_add_and_thread(self):
        comment = self.store.add(
            self.draft_id, "REL-1", "Ada", "hello", now="2026-01-01T00:00:00+00:00"
        )
        self.assertEqual(comment["id"], "c1")
        thread = self.store.thread(self.draft_id)
        self.assertEqual(len(thread["comments"]), 1)
        self.assertEqual(thread["comments"][0]["body"], "hello")

    def test_resolve_updates_thread(self):
        self.store.add(self.draft_id, "REL-1", "Ada", "a")
        self.store.resolve(self.draft_id, "c1", True)
        thread = self.store.thread(self.draft_id)
        self.assertTrue(thread["comments"][0]["resolved"])

    def test_resolve_is_idempotent(self):
        self.store.add(self.draft_id, "REL-1", "Ada", "a")
        self.store.resolve(self.draft_id, "c1", True)
        self.store.resolve(self.draft_id, "c1", True)
        thread = self.store.thread(self.draft_id)
        self.assertTrue(thread["comments"][0]["resolved"])
        self.assertEqual(len(thread["comments"]), 1)

    def test_unknown_comment_raises(self):
        with self.assertRaises(LookupError):
            self.store.resolve(self.draft_id, "nope", True)

    def test_resolve_trims_comment_id(self):
        self.store.add(self.draft_id, "REL-1", "Ada", "a")
        comment = self.store.resolve(self.draft_id, "  c1  ", True)
        self.assertEqual(comment["id"], "c1")
        self.assertTrue(comment["resolved"])
        thread = self.store.thread(self.draft_id)
        self.assertTrue(thread["comments"][0]["resolved"])
        other = CommentStore(self.tmp.name, self.drafts)
        self.assertTrue(other.thread(self.draft_id)["comments"][0]["resolved"])

    def test_resolve_blank_comment_id_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.store.resolve(self.draft_id, "   ", True)

    def test_persists_across_instances(self):
        self.store.add(
            self.draft_id,
            "REL-1",
            "Ada",
            "persisted",
            now="2026-01-01T00:00:00+00:00",
        )
        self.store.resolve(self.draft_id, "c1", True)
        other = CommentStore(self.tmp.name, self.drafts)
        thread = other.thread(self.draft_id)
        self.assertEqual(thread["comments"][0]["body"], "persisted")
        self.assertTrue(thread["comments"][0]["resolved"])

    def test_log_is_append_only(self):
        self.store.add(self.draft_id, "REL-1", "Ada", "a")
        self.store.resolve(self.draft_id, "c1", True)
        path = os.path.join(self.tmp.name, "comments.jsonl")
        with open(path, encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
        self.assertEqual(len(lines), 2)

    def test_all_comments_across_drafts(self):
        second = self.drafts.save("R2", "# R2", 1, 0)["id"]
        self.store.add(self.draft_id, "REL-1", "Ada", "first")
        self.store.add(second, "REL-2", "Bob", "second")
        comments = self.store.all_comments()
        self.assertEqual([c["body"] for c in comments], ["first", "second"])
        self.assertEqual(comments[0]["draft_id"], self.draft_id)

    def test_has_draft_checks_registry(self):
        self.assertTrue(self.store.has_draft(self.draft_id))
        self.assertFalse(self.store.has_draft("does-not-exist"))


class CommentStoreInMemoryTests(unittest.TestCase):
    def setUp(self):
        self.store = CommentStore()

    def test_not_durable_without_data_dir(self):
        self.assertFalse(self.store.is_durable)

    def test_round_trip_without_data_dir(self):
        self.store.add("ephemeral", "REL-1", "Ada", "hello")
        self.store.resolve("ephemeral", "c1", True)
        thread = self.store.thread("ephemeral")
        self.assertEqual(thread["comments"][0]["body"], "hello")
        self.assertTrue(thread["comments"][0]["resolved"])

    def test_accepts_any_draft_without_registry(self):
        self.assertTrue(self.store.has_draft("anything"))


if __name__ == "__main__":
    unittest.main()
