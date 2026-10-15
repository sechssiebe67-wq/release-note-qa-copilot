import unittest

from releasenote.importer import (
    ParseError,
    normalize_type,
    parse_csv,
    parse_entries,
    parse_json,
)
from releasenote.models import TYPE_BREAKING, TYPE_FEATURE, TYPE_FIX, TYPE_OTHER


CSV_TEXT = (
    "id,type,title,description,benefit,source,link,audience\n"
    "REL-1,feature,Bulk export,Export all reports,Save time,REL-1,,admins\n"
    "REL-2,Bugfix,Fix crash,Null check added,Stability,REL-2,,everyone\n"
)


class NormalizeTypeTests(unittest.TestCase):
    def test_known_aliases(self):
        self.assertEqual(normalize_type("Feature"), TYPE_FEATURE)
        self.assertEqual(normalize_type("bug fix"), TYPE_FIX)
        self.assertEqual(normalize_type("breaking_change"), TYPE_BREAKING)
        self.assertEqual(normalize_type("chore"), TYPE_OTHER)

    def test_unknown_and_none(self):
        self.assertEqual(normalize_type("mystery"), TYPE_OTHER)
        self.assertEqual(normalize_type(None), TYPE_OTHER)


class ParseCsvTests(unittest.TestCase):
    def test_parses_rows_and_aliases(self):
        entries = parse_csv(CSV_TEXT)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].id, "REL-1")
        self.assertEqual(entries[0].type, TYPE_FEATURE)
        self.assertEqual(entries[0].title, "Bulk export")
        self.assertEqual(entries[1].type, TYPE_FIX)

    def test_skips_blank_rows(self):
        text = CSV_TEXT + ",,,,,\n"
        self.assertEqual(len(parse_csv(text)), 2)

    def test_rejects_empty(self):
        with self.assertRaises(ParseError):
            parse_csv("")

    def test_rejects_unknown_header(self):
        with self.assertRaises(ParseError):
            parse_csv("foo,bar\n1,2\n")


class ParseJsonTests(unittest.TestCase):
    def test_list_payload(self):
        entries = parse_json('[{"id": "A", "type": "fix", "title": "T"}]')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].type, TYPE_FIX)

    def test_entries_object(self):
        entries = parse_json('{"entries": [{"title": "X"}, {"title": "Y"}]}')
        self.assertEqual([e.title for e in entries], ["X", "Y"])

    def test_single_object(self):
        entries = parse_json('{"title": "Solo", "type": "feature"}')
        self.assertEqual(entries[0].title, "Solo")

    def test_invalid_json(self):
        with self.assertRaises(ParseError):
            parse_json("{not json")

    def test_rejects_unsupported_shape(self):
        with self.assertRaises(ParseError):
            parse_json('{"foo": 1}')


class AutoDetectTests(unittest.TestCase):
    def test_detects_json(self):
        self.assertEqual(
            len(parse_entries('[{"title": "T"}]', "auto")), 1
        )

    def test_empty_json_entries_raise(self):
        with self.assertRaises(ParseError):
            parse_entries('{"entries": []}', "auto")

    def test_detects_csv(self):
        self.assertEqual(len(parse_entries(CSV_TEXT, "auto")), 2)

    def test_explicit_format(self):
        with self.assertRaises(ParseError):
            parse_entries(CSV_TEXT, "xml")


if __name__ == "__main__":
    unittest.main()
