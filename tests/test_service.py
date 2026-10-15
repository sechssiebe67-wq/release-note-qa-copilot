import unittest

from releasenote.importer import ParseError
from releasenote.service import build_draft, sample


class BuildDraftTests(unittest.TestCase):
    def test_builds_complete_result(self):
        result = build_draft(sample("csv")["content"], "csv", "Release 3.0")
        self.assertEqual(result["title"], "Release 3.0")
        self.assertEqual(result["entry_count"], 4)
        self.assertIn("markdown", result)
        self.assertIn("qa", result)
        self.assertIn("checklist", result)
        self.assertEqual(len(result["groups"]), 4)

    def test_groups_have_titles(self):
        result = build_draft(sample("json")["content"], "json")
        titles = [g["title"] for g in result["groups"]]
        self.assertIn("Features", titles)
        self.assertIn("Breaking Changes", titles)

    def test_auto_detect(self):
        result = build_draft(sample("json")["content"], "auto")
        self.assertEqual(result["entry_count"], 2)

    def test_invalid_input_raises(self):
        with self.assertRaises(ParseError):
            build_draft("", "csv")

    def test_blank_title_falls_back(self):
        result = build_draft(sample("csv")["content"], "csv", "   ")
        self.assertEqual(result["title"], "Release Notes")

    def test_qa_publishable_for_clean_input(self):
        content = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Fix login,Login no longer fails,Reliable access,A\n"
        )
        result = build_draft(content, "csv")
        self.assertTrue(result["qa"]["publishable"])


class SampleTests(unittest.TestCase):
    def test_csv_sample(self):
        self.assertEqual(sample("csv")["format"], "csv")
        self.assertIn("id,type,title", sample("csv")["content"])

    def test_json_sample(self):
        self.assertEqual(sample("json")["format"], "json")
        self.assertIn("entries", sample("json")["content"])

    def test_default_is_csv(self):
        self.assertEqual(sample()["format"], "csv")


if __name__ == "__main__":
    unittest.main()
