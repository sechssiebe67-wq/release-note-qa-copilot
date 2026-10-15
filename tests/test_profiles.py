import json
import unittest

from releasenote.config import WorkspaceConfig
from releasenote.models import ChangeEntry
from releasenote.profiles import (
    BUILTIN_PROFILES,
    RuleProfile,
    UnknownProfileError,
    get_profile,
    list_profiles,
    normalize_profile_name,
    resolve_config,
    rule_summary,
)
from releasenote.qa import analyze_entries

VAGUE_CONTENT = (
    "id,type,title,description,benefit,source\n"
    "A,fix,Better sync,The sync is improved,A much better experience,A\n"
)


class BuiltinProfileTests(unittest.TestCase):
    def test_at_least_three_profiles(self):
        self.assertGreaterEqual(len(BUILTIN_PROFILES), 3)

    def test_expected_profiles_present(self):
        for name in ("strict", "marketing-safe", "internal"):
            self.assertIn(name, BUILTIN_PROFILES)

    def test_profiles_are_valid_configs_with_metadata(self):
        for name, profile in BUILTIN_PROFILES.items():
            self.assertIsInstance(profile, RuleProfile)
            self.assertEqual(profile.name, name)
            self.assertTrue(profile.description)
            self.assertIsInstance(profile.config, WorkspaceConfig)

    def test_round_trip_through_to_dict(self):
        for profile in BUILTIN_PROFILES.values():
            payload = profile.to_dict()
            self.assertEqual(set(payload), {"name", "description", "config"})
            rebuilt = WorkspaceConfig.from_dict(payload["config"])
            self.assertEqual(rebuilt, profile.config)

    def test_list_profiles_is_json_serialisable(self):
        profiles = list_profiles()
        self.assertEqual(len(profiles), len(BUILTIN_PROFILES))
        self.assertEqual(
            [item["name"] for item in profiles],
            sorted(BUILTIN_PROFILES),
        )
        for item in profiles:
            self.assertEqual(set(item), {"name", "description", "config"})
        json.dumps(profiles)

    def test_get_profile_unknown_raises(self):
        with self.assertRaises(ValueError):
            get_profile("nope")

    def test_get_profile_empty_raises(self):
        with self.assertRaises(UnknownProfileError):
            get_profile("   ")

    def test_strict_reranks_vague_and_requires_breaking(self):
        config = get_profile("strict").config
        self.assertEqual(
            config.severity_overrides.get("vague_language"), "high"
        )
        self.assertIn("breaking", config.required_sections)

    def test_marketing_safe_bans_phrases(self):
        config = get_profile("marketing-safe").config
        self.assertTrue(config.banned_phrases)

    def test_internal_allows_terms(self):
        config = get_profile("internal").config
        self.assertIn("misc", config.allow_terms)


class NormalizeProfileNameTests(unittest.TestCase):
    def test_none_and_empty(self):
        self.assertIsNone(normalize_profile_name(None))
        self.assertIsNone(normalize_profile_name(""))
        self.assertIsNone(normalize_profile_name("   "))

    def test_trimmed(self):
        self.assertEqual(normalize_profile_name("  strict  "), "strict")

    def test_non_string_raises(self):
        with self.assertRaises(ValueError):
            normalize_profile_name(3)


class ResolveConfigTests(unittest.TestCase):
    def test_profile_only(self):
        config = resolve_config({"profile": "strict"})
        self.assertEqual(config.severity_overrides.get("vague_language"), "high")
        self.assertIn("breaking", config.required_sections)

    def test_config_only(self):
        config = resolve_config(
            {"config": {"extra_vague_terms": ["blazing"]}}
        )
        self.assertEqual(config.extra_vague_terms, ["blazing"])

    def test_none_and_empty_are_default(self):
        self.assertEqual(resolve_config(None), WorkspaceConfig.default())
        self.assertEqual(resolve_config({}), WorkspaceConfig.default())
        self.assertTrue(resolve_config({}).is_default())

    def test_layered_profile_then_inline(self):
        config = resolve_config(
            {
                "profile": "strict",
                "config": {
                    "extra_vague_terms": ["blazing"],
                    "severity_overrides": {"missing_source": "low"},
                },
            }
        )
        self.assertIn("blazing", config.extra_vague_terms)
        self.assertIn("breaking", config.required_sections)
        self.assertEqual(config.severity_overrides["vague_language"], "high")
        self.assertEqual(config.severity_overrides["missing_source"], "low")

    def test_unknown_profile_raises(self):
        with self.assertRaises(UnknownProfileError):
            resolve_config({"profile": "nope"})
        with self.assertRaises(ValueError):
            resolve_config({"profile": "nope"})

    def test_invalid_config_raises(self):
        with self.assertRaises(ValueError):
            resolve_config({"config": {"nope": []}})
        with self.assertRaises(ValueError):
            resolve_config({"config": ["not", "an", "object"]})

    def test_profile_config_is_not_shared(self):
        first = resolve_config({"profile": "strict"})
        first.extra_vague_terms.append("mutated")
        second = resolve_config({"profile": "strict"})
        self.assertNotIn("mutated", second.extra_vague_terms)
        self.assertNotIn(
            "mutated", BUILTIN_PROFILES["strict"].config.extra_vague_terms
        )


class RuleSummaryTests(unittest.TestCase):
    def _entries(self):
        return [
            ChangeEntry(
                id="A",
                type="feature",
                title="Better sync",
                description="The sync is improved",
                benefit="A much better experience",
                source="A",
            )
        ]

    def test_deterministic(self):
        entries = self._entries()
        config = get_profile("strict").config
        qa = analyze_entries(entries, config)
        first = rule_summary(entries, qa, config)
        second = rule_summary(entries, qa, config)
        self.assertEqual(first, second)
        self.assertEqual(first, rule_summary(entries, qa, config))

    def test_counts_severities_and_overrides(self):
        entries = self._entries()
        config = get_profile("strict").config
        qa = analyze_entries(entries, config)
        summary = rule_summary(entries, qa, config)
        self.assertEqual(summary["flag_counts"]["vague_language"], 1)
        self.assertEqual(
            summary["flag_severities"]["vague_language"], "high"
        )
        self.assertEqual(
            summary["severity_overrides"], {"vague_language": "high"}
        )
        self.assertEqual(summary["required_sections"], ["breaking"])
        self.assertIn("better", summary["effective_vague_terms"])
        self.assertGreaterEqual(summary["flag_counts"]["missing_required_section"], 1)
        self.assertEqual(summary["entry_count"], 1)
        self.assertEqual(
            summary["total_flags"], sum(summary["flag_counts"].values())
        )

    def test_empty_flags(self):
        entries = [
            ChangeEntry(
                id="A",
                type="fix",
                title="Fix login",
                description="Login no longer fails",
                benefit="Reliable access",
                source="A",
            )
        ]
        config = WorkspaceConfig.default()
        qa = analyze_entries(entries, config)
        summary = rule_summary(entries, qa, config)
        self.assertEqual(summary["flag_counts"], {})
        self.assertEqual(summary["flag_severities"], {})
        self.assertEqual(summary["total_flags"], 0)

    def test_json_serialisable(self):
        entries = self._entries()
        config = WorkspaceConfig.default()
        qa = analyze_entries(entries, config)
        json.dumps(rule_summary(entries, qa, config))


if __name__ == "__main__":
    unittest.main()
