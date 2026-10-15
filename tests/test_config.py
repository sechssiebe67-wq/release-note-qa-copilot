import unittest

from releasenote.config import WorkspaceConfig, sample_config


class WorkspaceConfigTests(unittest.TestCase):
    def test_default_is_empty(self):
        config = WorkspaceConfig.default()
        self.assertEqual(config.extra_vague_terms, [])
        self.assertEqual(config.allow_terms, [])
        self.assertEqual(config.required_sections, [])
        self.assertEqual(config.banned_phrases, [])
        self.assertEqual(config.severity_overrides, {})
        self.assertTrue(config.is_default())

    def test_from_dict_none_is_default(self):
        self.assertEqual(WorkspaceConfig.from_dict(None), WorkspaceConfig.default())

    def test_from_dict_parses_all_fields(self):
        config = WorkspaceConfig.from_dict(
            {
                "extra_vague_terms": ["blazing", " revolutionary "],
                "allow_terms": ["seamless"],
                "required_sections": ["feature", "breaking"],
                "banned_phrases": ["world's best"],
                "severity_overrides": {"vague_language": "high"},
            }
        )
        self.assertEqual(config.extra_vague_terms, ["blazing", "revolutionary"])
        self.assertEqual(config.allow_terms, ["seamless"])
        self.assertEqual(config.required_sections, ["feature", "breaking"])
        self.assertEqual(config.banned_phrases, ["world's best"])
        self.assertEqual(config.severity_overrides, {"vague_language": "high"})
        self.assertFalse(config.is_default())

    def test_to_dict_round_trip(self):
        payload = {
            "extra_vague_terms": ["blazing"],
            "allow_terms": ["seamless"],
            "required_sections": ["fix"],
            "banned_phrases": ["guaranteed"],
            "severity_overrides": {"missing_source": "low"},
        }
        config = WorkspaceConfig.from_dict(payload)
        self.assertEqual(config.to_dict(), payload)
        self.assertEqual(
            WorkspaceConfig.from_dict(config.to_dict()), config
        )

    def test_unknown_key_raises(self):
        with self.assertRaises(ValueError):
            WorkspaceConfig.from_dict({"nope": []})

    def test_non_object_raises(self):
        with self.assertRaises(ValueError):
            WorkspaceConfig.from_dict(["not", "an", "object"])

    def test_list_field_must_be_list(self):
        for key in (
            "extra_vague_terms",
            "allow_terms",
            "required_sections",
            "banned_phrases",
        ):
            with self.assertRaises(ValueError):
                WorkspaceConfig.from_dict({key: "not-a-list"})

    def test_list_field_must_contain_strings(self):
        with self.assertRaises(ValueError):
            WorkspaceConfig.from_dict({"extra_vague_terms": ["ok", 3]})

    def test_invalid_severity_raises(self):
        with self.assertRaises(ValueError):
            WorkspaceConfig.from_dict({"severity_overrides": {"vague_language": "critical"}})

    def test_severity_overrides_must_be_object(self):
        with self.assertRaises(ValueError):
            WorkspaceConfig.from_dict({"severity_overrides": ["high"]})

    def test_severity_overrides_keys_must_be_strings(self):
        with self.assertRaises(ValueError):
            WorkspaceConfig.from_dict({"severity_overrides": {1: "high"}})

    def test_sample_config_is_valid(self):
        payload = sample_config()
        config = WorkspaceConfig.from_dict(payload)
        self.assertEqual(config.extra_vague_terms, ["blazing", "revolutionary"])
        self.assertEqual(config.severity_overrides, {"vague_language": "high"})


if __name__ == "__main__":
    unittest.main()
