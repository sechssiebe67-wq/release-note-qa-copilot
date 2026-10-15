import os
import tempfile
import unittest
from unittest import mock

from releasenote.models import (
    REVIEW_APPROVED,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    ReviewRecord,
)
from releasenote.service import get_review, record_review
from releasenote.storage import DraftStore, ReviewStore


class ReviewRecordTests(unittest.TestCase):
    def test_to_dict_shape(self):
        record = ReviewRecord(
            id="d1",
            status=REVIEW_APPROVED,
            reviewer="Ada",
            comment="Looks good",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(
            record.to_dict(),
            {
                "id": "d1",
                "status": "approved",
                "reviewer": "Ada",
                "comment": "Looks good",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        )

    def test_defaults_to_pending(self):
        record = ReviewRecord(id="d1")
        self.assertEqual(record.status, REVIEW_PENDING)
        self.assertEqual(record.to_dict()["status"], "pending")


class _ReviewHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.drafts = DraftStore(self.tmp.name)
        self.draft = self.drafts.save("Release", "# Release", 1, 0)
        self.draft_id = self.draft["id"]
        self.store = ReviewStore(self.tmp.name, self.drafts)

    def tearDown(self):
        self.tmp.cleanup()


class RecordReviewTests(_ReviewHarness):
    def test_valid_transitions(self):
        record = record_review(
            self.draft_id, "approved", "Ada", "Ship it", self.store
        )
        self.assertEqual(record.status, REVIEW_APPROVED)
        self.assertEqual(record.reviewer, "Ada")
        self.assertEqual(record.comment, "Ship it")
        self.assertTrue(record.updated_at)

        record = record_review(
            self.draft_id, "rejected", "Bob", "Needs work", self.store
        )
        self.assertEqual(record.status, REVIEW_REJECTED)

        record = record_review(self.draft_id, "pending", "", "", self.store)
        self.assertEqual(record.status, REVIEW_PENDING)

    def test_status_is_case_insensitive(self):
        record = record_review(self.draft_id, "APPROVED", store=self.store)
        self.assertEqual(record.status, REVIEW_APPROVED)

    def test_unknown_status_raises_value_error(self):
        with self.assertRaises(ValueError):
            record_review(self.draft_id, "bogus", store=self.store)

    def test_empty_draft_id_raises_value_error(self):
        with self.assertRaises(ValueError):
            record_review("", "approved", store=self.store)

    def test_unknown_draft_id_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            record_review("does-not-exist", "approved", store=self.store)

    def test_audit_grows_by_one_per_call(self):
        for i in range(3):
            record_review(
                self.draft_id, "approved", "Ada", "note %d" % i, self.store
            )
            self.assertEqual(len(self.store.audit(self.draft_id)), i + 1)

    def test_audit_order_and_timestamps_are_stable(self):
        record_review(self.draft_id, "approved", "Ada", store=self.store)
        record_review(self.draft_id, "rejected", "Bob", store=self.store)
        audit = self.store.audit(self.draft_id)
        self.assertEqual([e["status"] for e in audit], ["approved", "rejected"])
        self.assertEqual([e["seq"] for e in audit], [1, 2])
        self.assertLessEqual(audit[0]["updated_at"], audit[1]["updated_at"])
        self.assertEqual(audit[0]["previous_status"], REVIEW_PENDING)
        self.assertEqual(audit[1]["previous_status"], REVIEW_APPROVED)

    def test_failed_call_appends_nothing(self):
        with self.assertRaises(ValueError):
            record_review(self.draft_id, "bogus", store=self.store)
        with self.assertRaises(LookupError):
            record_review("nope", "approved", store=self.store)
        self.assertEqual(self.store.audit(self.draft_id), [])

    def test_get_review_pending_without_transition(self):
        review = get_review(self.draft_id, self.store)
        self.assertEqual(review["draft_id"], self.draft_id)
        self.assertEqual(review["status"], REVIEW_PENDING)
        self.assertEqual(review["audit"], [])

    def test_get_review_returns_current_status_and_audit(self):
        record_review(self.draft_id, "approved", "Ada", "ok", self.store)
        record_review(self.draft_id, "rejected", "Bob", "no", self.store)
        review = get_review(self.draft_id, self.store)
        self.assertEqual(review["status"], REVIEW_REJECTED)
        self.assertEqual(review["reviewer"], "Bob")
        self.assertEqual(review["comment"], "no")
        self.assertEqual(
            [e["status"] for e in review["audit"]], ["approved", "rejected"]
        )

    def test_get_review_unknown_draft_raises(self):
        with self.assertRaises(LookupError):
            get_review("missing", self.store)

    def test_state_persists_across_instances(self):
        record_review(self.draft_id, "approved", "Ada", store=self.store)
        other = ReviewStore(self.tmp.name, DraftStore(self.tmp.name))
        review = get_review(self.draft_id, other)
        self.assertEqual(review["status"], REVIEW_APPROVED)
        self.assertEqual(len(review["audit"]), 1)


class InMemoryReviewTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("DATA_DIR", None)
        self.store = ReviewStore(None)

    def tearDown(self):
        self.env.stop()

    def test_record_and_get_without_data_dir(self):
        record = record_review("draft-1", "approved", "Ada", "ok", self.store)
        self.assertEqual(record.status, REVIEW_APPROVED)
        review = get_review("draft-1", self.store)
        self.assertEqual(review["status"], REVIEW_APPROVED)
        self.assertEqual(len(review["audit"]), 1)

    def test_unknown_status_still_raises_in_memory(self):
        with self.assertRaises(ValueError):
            record_review("draft-1", "nope", store=self.store)

    def test_records_are_lost_on_restart(self):
        record_review("draft-1", "approved", store=self.store)
        fresh = ReviewStore(None)
        self.assertEqual(fresh.audit("draft-1"), [])
        self.assertIsNone(fresh.get("draft-1"))


if __name__ == "__main__":
    unittest.main()
