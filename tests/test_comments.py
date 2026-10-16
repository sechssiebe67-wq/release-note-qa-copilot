import unittest

from releasenote.comments import (
    ANONYMOUS_AUTHOR,
    MAX_COMMENT_LENGTH,
    add_comment,
    normalize_author,
    resolve_comment,
    summarize_thread,
    validate_comment_body,
    validate_entry_id,
)

NOW = "2026-01-01T00:00:00+00:00"


def _empty():
    return {"draft_id": "d1", "comments": []}


class ValidateBodyTests(unittest.TestCase):
    def test_trims_and_returns_string(self):
        self.assertEqual(validate_comment_body("  hello  "), "hello")

    def test_rejects_blank(self):
        for value in ("", "   ", "\n\t"):
            with self.assertRaises(ValueError):
                validate_comment_body(value)

    def test_rejects_non_string(self):
        for value in (None, 5, ["x"], {"body": "x"}, True):
            with self.assertRaises(ValueError):
                validate_comment_body(value)

    def test_rejects_over_long(self):
        with self.assertRaises(ValueError):
            validate_comment_body("x" * (MAX_COMMENT_LENGTH + 1))

    def test_accepts_max_length(self):
        self.assertEqual(
            len(validate_comment_body("x" * MAX_COMMENT_LENGTH)),
            MAX_COMMENT_LENGTH,
        )


class NormalizeAuthorTests(unittest.TestCase):
    def test_trims(self):
        self.assertEqual(normalize_author("  Ada  "), "Ada")

    def test_defaults_to_anonymous(self):
        self.assertEqual(normalize_author(None), ANONYMOUS_AUTHOR)
        self.assertEqual(normalize_author(""), ANONYMOUS_AUTHOR)
        self.assertEqual(normalize_author("   "), ANONYMOUS_AUTHOR)

    def test_rejects_non_string(self):
        with self.assertRaises(ValueError):
            normalize_author(42)


class ValidateEntryIdTests(unittest.TestCase):
    def test_requires_non_empty_string(self):
        self.assertEqual(validate_entry_id(" REL-1 "), "REL-1")
        for value in (None, "", "   ", 5):
            with self.assertRaises(ValueError):
                validate_entry_id(value)


class AddCommentTests(unittest.TestCase):
    def test_returns_new_thread_and_comment(self):
        thread = _empty()
        new_thread, comment = add_comment(
            thread, "REL-1", "Ada", "  first  ", now=NOW
        )
        self.assertEqual(thread["comments"], [])
        self.assertEqual(len(new_thread["comments"]), 1)
        self.assertEqual(comment["id"], "c1")
        self.assertEqual(comment["entry_id"], "REL-1")
        self.assertEqual(comment["author"], "Ada")
        self.assertEqual(comment["body"], "first")
        self.assertEqual(comment["parent_id"], None)
        self.assertFalse(comment["resolved"])
        self.assertEqual(comment["created_at"], NOW)

    def test_ids_are_deterministic_and_ordered(self):
        thread = _empty()
        thread, first = add_comment(thread, "REL-1", "Ada", "a", now=NOW)
        thread, second = add_comment(thread, "REL-2", "Bob", "b", now=NOW)
        thread, third = add_comment(thread, "REL-1", "Ada", "c", now=NOW)
        self.assertEqual([first["id"], second["id"], third["id"]],
                         ["c1", "c2", "c3"])
        self.assertEqual([c["id"] for c in thread["comments"]],
                         ["c1", "c2", "c3"])

    def test_same_input_same_output(self):
        def build(now):
            thread = _empty()
            thread, _ = add_comment(thread, "A", "Ada", "x", now=now)
            thread, _ = add_comment(thread, "A", "Bob", "y", parent_id="c1", now=now)
            return thread

        self.assertEqual(build(NOW), build(NOW))
        # With no explicit time the ids and ordering are still deterministic.
        self.assertEqual(build(None), build(None))

    def test_threading_with_parent(self):
        thread = _empty()
        thread, parent = add_comment(thread, "REL-1", "Ada", "root", now=NOW)
        thread, reply = add_comment(
            thread, "REL-1", "Bob", "reply", parent_id=parent["id"], now=NOW
        )
        self.assertEqual(reply["parent_id"], "c1")

    def test_unknown_parent_raises_and_is_not_added(self):
        thread = _empty()
        with self.assertRaises(LookupError):
            add_comment(thread, "REL-1", "Ada", "x", parent_id="missing", now=NOW)
        self.assertEqual(thread["comments"], [])

    def test_invalid_body_raises(self):
        with self.assertRaises(ValueError):
            add_comment(_empty(), "REL-1", "Ada", "   ", now=NOW)


class ResolveCommentTests(unittest.TestCase):
    def setUp(self):
        thread = _empty()
        thread, self.first = add_comment(thread, "REL-1", "Ada", "a", now=NOW)
        thread, self.second = add_comment(thread, "REL-1", "Bob", "b", now=NOW)
        self.thread = thread

    def test_sets_resolved_flag(self):
        updated = resolve_comment(self.thread, self.first["id"], True)
        self.assertTrue(updated["comments"][0]["resolved"])
        self.assertFalse(self.thread["comments"][0]["resolved"])

    def test_idempotent(self):
        once = resolve_comment(self.thread, "c1", True)
        twice = resolve_comment(once, "c1", True)
        self.assertEqual(once, twice)

    def test_can_reopen(self):
        resolved = resolve_comment(self.thread, "c1", True)
        reopened = resolve_comment(resolved, "c1", False)
        self.assertFalse(reopened["comments"][0]["resolved"])

    def test_unknown_comment_raises(self):
        with self.assertRaises(LookupError):
            resolve_comment(self.thread, "c99", True)

    def test_non_boolean_raises(self):
        with self.assertRaises(ValueError):
            resolve_comment(self.thread, "c1", "yes")


class SummarizeThreadTests(unittest.TestCase):
    def test_counts_per_entry_and_participants(self):
        thread = _empty()
        thread, _ = add_comment(thread, "REL-1", "Ada", "a", now=NOW)
        thread, _ = add_comment(thread, "REL-1", "Bob", "b", now=NOW)
        thread, _ = add_comment(thread, "REL-2", "Ada", "c", now=NOW)
        thread = resolve_comment(thread, "c1", True)
        summary = summarize_thread(thread)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["resolved"], 1)
        self.assertEqual(summary["unresolved"], 2)
        self.assertEqual(summary["entries"]["REL-1"]["total"], 2)
        self.assertEqual(summary["entries"]["REL-1"]["unresolved"], 1)
        self.assertEqual(summary["entries"]["REL-1"]["participants"], ["Ada", "Bob"])
        self.assertEqual(list(summary["entries"]), ["REL-1", "REL-2"])

    def test_empty_thread(self):
        summary = summarize_thread(_empty())
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["unresolved"], 0)
        self.assertEqual(summary["entries"], {})


if __name__ == "__main__":
    unittest.main()
