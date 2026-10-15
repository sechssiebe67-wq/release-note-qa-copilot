import unittest

from releasenote.config import WorkspaceConfig, sample_config
from releasenote.importer import ParseError
from releasenote.service import build_draft, sample, sample_workspace_config


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


class WorkspaceConfigServiceTests(unittest.TestCase):
    def test_default_config_is_echoed(self):
        result = build_draft(sample("csv")["content"], "csv")
        self.assertEqual(result["config"], WorkspaceConfig.default().to_dict())

    def test_config_round_trip_from_mapping(self):
        payload = {
            "extra_vague_terms": ["blazing"],
            "severity_overrides": {"vague_language": "high"},
        }
        result = build_draft(sample("csv")["content"], "csv", config=payload)
        self.assertEqual(result["config"]["extra_vague_terms"], ["blazing"])
        self.assertEqual(
            result["config"]["severity_overrides"], {"vague_language": "high"}
        )

    def test_config_applies_to_qa(self):
        content = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Blazing sync,Sync is blazing fast,Quick,A\n"
        )
        result = build_draft(
            content,
            "csv",
            config={
                "extra_vague_terms": ["blazing"],
                "severity_overrides": {"vague_language": "high"},
            },
        )
        flags = result["qa"]["flags"]
        self.assertEqual(flags[0]["code"], "vague_language")
        self.assertEqual(flags[0]["severity"], "high")
        self.assertFalse(result["qa"]["publishable"])

    def test_required_section_flag(self):
        content = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Fix login,Login no longer fails,Reliable access,A\n"
        )
        result = build_draft(
            content, "csv", config={"required_sections": ["breaking"]}
        )
        flags = result["qa"]["flags"]
        self.assertTrue(any(f["code"] == "missing_required_section" for f in flags))

    def test_accepts_workspace_config_object(self):
        config = WorkspaceConfig.from_dict({"extra_vague_terms": ["blazing"]})
        result = build_draft(sample("csv")["content"], "csv", config=config)
        self.assertEqual(result["config"], config.to_dict())

    def test_invalid_config_raises_value_error(self):
        with self.assertRaises(ValueError):
            build_draft(sample("csv")["content"], "csv", config={"nope": []})

    def test_no_config_matches_default_config_byte_for_byte(self):
        content = sample("csv")["content"]
        without = build_draft(content, "csv")
        default = build_draft(content, "csv", config=WorkspaceConfig.default())
        self.assertEqual(without["markdown"], default["markdown"])
        self.assertEqual(without["qa"], default["qa"])

    def test_no_config_regression_flag_set_unchanged(self):
        # Snapshot of the built-in heuristics output for the CSV sample. If this
        # changes, the default QA behaviour changed and needs review.
        result = build_draft(sample("csv")["content"], "csv")
        codes = sorted(f["code"] for f in result["qa"]["flags"])
        self.assertEqual(codes, ["vague_language", "vague_language"])
        self.assertTrue(result["qa"]["publishable"])

    def test_sample_workspace_config_is_valid(self):
        payload = sample_workspace_config()
        config = WorkspaceConfig.from_dict(payload)
        self.assertEqual(payload, config.to_dict())
        self.assertEqual(payload, sample_config())


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
