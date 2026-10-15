import os
import tempfile
import unittest

from releasenote.storage import DraftStore


class DraftStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = DraftStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_get(self):
        record = self.store.save("R1", "# R1", 3, 1)
        self.assertIn("id", record)
        fetched = self.store.get(record["id"])
        self.assertEqual(fetched["markdown"], "# R1")
        self.assertEqual(fetched["entry_count"], 3)

    def test_list_newest_first(self):
        self.store.save("first", "a", 1, 0)
        self.store.save("second", "b", 2, 0)
        listing = self.store.list()
        self.assertEqual([d["title"] for d in listing], ["second", "first"])
        # summaries exclude the full markdown
        self.assertNotIn("markdown", listing[0])

    def test_list_limit(self):
        for i in range(5):
            self.store.save("t%d" % i, "m", 1, 0)
        self.assertEqual(len(self.store.list(limit=2)), 2)

    def test_max_entries_trimmed(self):
        store = DraftStore(self.tmp.name, max_entries=3)
        for i in range(6):
            store.save("t%d" % i, "m", 1, 0)
        titles = [d["title"] for d in store.list(limit=100)]
        self.assertEqual(titles, ["t5", "t4", "t3"])

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.store.get("nope"))

    def test_persists_across_instances(self):
        record = self.store.save("persisted", "x", 1, 0)
        other = DraftStore(self.tmp.name)
        self.assertEqual(other.get(record["id"])["title"], "persisted")

    def test_creates_directory(self):
        nested = os.path.join(self.tmp.name, "deep", "data")
        store = DraftStore(nested)
        store.save("t", "m", 1, 0)
        self.assertTrue(os.path.isdir(nested))


if __name__ == "__main__":
    unittest.main()
