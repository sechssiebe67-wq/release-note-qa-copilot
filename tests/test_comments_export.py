import csv
import io
import json
import unittest

from releasenote.comments import add_comment, resolve_comment, summarize_thread
from releasenote.exporters import COMMENTS_HEADER, comments_csv, comments_json


def _thread():
    thread = {"draft_id": "d1", "comments": []}
    thread, _ = add_comment(
        thread, "REL-1", "Ada", "first, with comma", now="2026-01-01T00:00:00+00:00"
    )
    thread, _ = add_comment(
        thread,
        "REL-1",
        "Bob",
        'reply "quoted"\nnewline',
        parent_id="c1",
        now="2026-01-01T00:01:00+00:00",
    )
    thread = resolve_comment(thread, "c1", True)
    return thread


def _payload(thread):
    return {
        "draft_id": thread.get("draft_id"),
        "comments": thread["comments"],
        "summary": summarize_thread(thread),
    }


class CommentsCsvTests(unittest.TestCase):
    def test_header(self):
        self.assertEqual(
            list(COMMENTS_HEADER),
            [
                "entry_id",
                "comment_id",
                "parent_id",
                "author",
                "created_at",
                "resolved",
                "body",
            ],
        )

    def test_one_row_per_comment(self):
        payload = _payload(_thread())
        rows = list(csv.reader(io.StringIO(comments_csv(payload))))
        self.assertEqual(rows[0], list(COMMENTS_HEADER))
        self.assertEqual(len(rows), 3)

    def test_round_trips_special_characters(self):
        rows = list(csv.reader(io.StringIO(comments_csv(_payload(_thread())))))
        self.assertEqual(rows[1][6], "first, with comma")
        self.assertEqual(rows[2][6], 'reply "quoted"\nnewline')
        self.assertEqual(rows[2][2], "c1")

    def test_resolved_rendered_as_python_bool(self):
        rows = list(csv.reader(io.StringIO(comments_csv(_payload(_thread())))))
        self.assertEqual(rows[1][5], "True")
        self.assertEqual(rows[2][5], "False")

    def test_empty_returns_header_only(self):
        rows = list(csv.reader(io.StringIO(comments_csv({"comments": []}))))
        self.assertEqual(rows, [list(COMMENTS_HEADER)])

    def test_missing_comments_returns_header_only(self):
        rows = list(csv.reader(io.StringIO(comments_csv({}))))
        self.assertEqual(rows, [list(COMMENTS_HEADER)])

    def test_uses_lf_line_endings(self):
        self.assertNotIn("\r\n", comments_csv(_payload(_thread())))


class CommentsJsonTests(unittest.TestCase):
    def test_matches_payload_and_sorted_keys(self):
        payload = _payload(_thread())
        decoded = json.loads(comments_json(payload))
        self.assertEqual(decoded, payload)
        self.assertEqual(
            comments_json(payload),
            json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False),
        )

    def test_utf8_is_preserved(self):
        payload = {"comments": [{"id": "c1", "body": "caf\u00e9"}]}
        self.assertIn("caf\u00e9", comments_json(payload))

    def test_empty_payload_is_empty_object(self):
        self.assertEqual(json.loads(comments_json({})), {})


if __name__ == "__main__":
    unittest.main()
