import tempfile
import unittest
from unittest import mock

from releasenote.config import WorkspaceConfig, sample_config
from releasenote.importer import ParseError
from releasenote.models import Flag, QAResult
from releasenote.profiles import resolve_config
from releasenote.service import (
    HistoryRequiredError,
    add_draft_comment,
    build_diff,
    build_draft,
    get_shared_draft,
    list_draft_comments,
    resolve_draft_comment,
    sample,
    sample_workspace_config,
)
from releasenote.storage import CommentStore, DraftStore, ReviewStore


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

    def test_clean_draft_scores_100_ready_and_passes_gate(self):
        content = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Fix login,Login no longer fails,Reliable access,A\n"
        )
        result = build_draft(content, "csv")
        self.assertEqual(result["score"]["score"], 100)
        self.assertEqual(result["score"]["band"], "ready")
        self.assertEqual(result["score"]["penalties"], [])
        self.assertEqual(result["gate"], {"status": "pass", "reasons": []})

    def test_score_and_gate_present_in_payload(self):
        result = build_draft(sample("csv")["content"], "csv")
        self.assertIn("score", result)
        self.assertIn("gate", result)
        self.assertEqual(result["gate"], result["score"]["gate"])

    def test_gate_fails_for_failing_policy(self):
        content = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Better sync,The sync is improved,"
            "A much better experience,A\n"
        )
        result = build_draft(
            content, "csv", config={"min_score": 90, "max_high": 0}
        )
        self.assertEqual(result["gate"]["status"], "fail")
        self.assertTrue(result["gate"]["reasons"])

    def test_strict_profile_gate_is_effective(self):
        content = (
            "id,type,title,description,benefit,source\n"
            "A,fix,Better sync,The sync is improved,"
            "A much better experience,A\n"
        )
        result = build_draft(content, "csv", config=resolve_config({"profile": "strict"}))
        self.assertEqual(result["config"]["min_score"], 85)
        self.assertEqual(result["gate"]["status"], "fail")


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
        # changes, the default QA behaviour changed and needs review. The CSV
        # sample contains two passive-voice descriptions ("are removed", "is
        # improved"), so the language lint contributes two low-severity flags.
        result = build_draft(sample("csv")["content"], "csv")
        codes = sorted(f["code"] for f in result["qa"]["flags"])
        self.assertEqual(
            codes,
            ["passive_voice", "passive_voice", "vague_language", "vague_language"],
        )
        self.assertTrue(result["qa"]["publishable"])
        self.assertIn("language", result)
        self.assertEqual(result["language"]["summary"]["counts"]["total"], 2)

    def test_sample_workspace_config_is_valid(self):
        payload = sample_workspace_config()
        config = WorkspaceConfig.from_dict(payload)
        self.assertEqual(payload, config.to_dict())
        self.assertEqual(payload, sample_config())


class BuildDiffTests(unittest.TestCase):
    PREVIOUS = (
        "id,type,title,description,benefit,source,link\n"
        "A,feature,Alpha,Alpha does things,Fast and reliable,SRC-A,https://a\n"
        "B,fix,Beta,Beta description,Gamma value,SRC-B,\n"
    )

    def test_build_diff_returns_standard_payload_and_diff(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Reliable and fast,,\n"
            "C,feature,Gamma,A new feature,New value,SRC-C,\n"
        )
        result = build_diff(self.PREVIOUS, current, "csv", "Release 4")
        for key in ("markdown", "qa", "checklist", "config", "diff"):
            self.assertIn(key, result)
        self.assertEqual(result["title"], "Release 4")
        self.assertEqual(result["entry_count"], 2)
        self.assertEqual(
            set(result["diff"]),
            {
                "added",
                "changed",
                "removed",
                "unchanged",
                "repeated_claims",
                "regressions",
            },
        )

    def test_build_diff_merges_new_flags(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Reliable and fast,,\n"
            "B,fix,Beta,Beta description,Gamma value,SRC-B,\n"
        )
        result = build_diff(self.PREVIOUS, current, "csv")
        codes = {f["code"] for f in result["qa"]["flags"]}
        self.assertIn("regression_unsourced", codes)
        self.assertIn("repeated_claim", codes)
        self.assertFalse(result["qa"]["publishable"])

    def test_build_diff_honours_severity_override(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Reliable and fast,,\n"
            "B,fix,Beta,Beta description,Gamma value,SRC-B,\n"
        )
        result = build_diff(
            self.PREVIOUS,
            current,
            "csv",
            config={"severity_overrides": {"regression_unsourced": "low"}},
        )
        flag = next(
            f
            for f in result["qa"]["flags"]
            if f["code"] == "regression_unsourced"
        )
        self.assertEqual(flag["severity"], "low")

    def test_build_diff_rejects_invalid_config(self):
        with self.assertRaises(ValueError):
            build_diff(
                self.PREVIOUS, self.PREVIOUS, "csv", config={"nope": []}
            )

    def test_build_draft_has_no_diff_key(self):
        result = build_draft(sample("csv")["content"], "csv")
        self.assertNotIn("diff", result)

    def test_build_diff_includes_score_and_gate(self):
        current = (
            "id,type,title,description,benefit,source,link\n"
            "A,feature,Alpha,Alpha does things,Reliable and fast,,\n"
            "B,fix,Beta,Beta description,Gamma value,SRC-B,\n"
        )
        result = build_diff(self.PREVIOUS, current, "csv")
        self.assertIn("score", result)
        self.assertIn("gate", result)
        self.assertEqual(result["gate"], result["score"]["gate"])
        # The regression flag from the diff is folded into the score.
        self.assertTrue(result["score"]["penalties"])
        self.assertFalse(result["qa"]["publishable"])


class GetSharedDraftTests(unittest.TestCase):
    VAGUE = (
        "id,type,title,description,benefit,source\n"
        "A,fix,Better sync,The sync is improved,"
        "A much better experience,A\n"
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = DraftStore(self.tmp.name)
        self.review_store = ReviewStore(self.tmp.name, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def _save(self, content=None, config=None):
        result = build_draft(content or sample("csv")["content"], "csv",
                             config=config)
        record = self.store.save(
            result["title"],
            result["markdown"],
            result["entry_count"],
            result["qa"]["counts"]["high"],
            entries=result["entries"],
            config=result["config"],
        )
        return result, record["id"]

    def test_payload_shape(self):
        _result, draft_id = self._save()
        payload = get_shared_draft(draft_id, self.store, self.review_store)
        for key in (
            "draft_id",
            "title",
            "markdown",
            "qa",
            "checklist",
            "rules",
            "review",
            "created_at",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["draft_id"], draft_id)
        self.assertIn("publishable", payload["qa"])
        self.assertIn("counts", payload["qa"])
        self.assertIn("flag_counts", payload["rules"])
        self.assertEqual(payload["review"]["status"], "pending")

    def test_markdown_and_title_match_stored_draft(self):
        result, draft_id = self._save()
        payload = get_shared_draft(draft_id, self.store)
        self.assertEqual(payload["title"], result["title"])
        self.assertEqual(payload["markdown"], result["markdown"])

    def test_qa_is_recomputed_not_read_from_record(self):
        _result, draft_id = self._save()
        marker = QAResult(flags=[Flag("marker", "high", "recomputed", "A")])
        with mock.patch("releasenote.service.analyze_entries", return_value=marker):
            payload = get_shared_draft(draft_id, self.store)
        self.assertEqual(payload["qa"]["counts"]["high"], 1)
        self.assertEqual(payload["qa"]["flags"][0]["code"], "marker")

    def test_uses_stored_config_for_rules(self):
        _result, draft_id = self._save(
            content=self.VAGUE,
            config={"severity_overrides": {"vague_language": "high"}},
        )
        payload = get_shared_draft(draft_id, self.store)
        vague = [f for f in payload["qa"]["flags"] if f["code"] == "vague_language"]
        self.assertTrue(vague)
        self.assertEqual(vague[0]["severity"], "high")
        self.assertEqual(
            payload["rules"]["severity_overrides"], {"vague_language": "high"}
        )

    def test_review_is_merged(self):
        _result, draft_id = self._save()
        self.review_store.record(draft_id, "approved", "Ada", "Ship it")
        payload = get_shared_draft(draft_id, self.store, self.review_store)
        self.assertEqual(payload["review"]["status"], "approved")
        self.assertEqual(payload["review"]["reviewer"], "Ada")
        self.assertEqual(payload["review"]["comment"], "Ship it")

    def test_unknown_id_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            get_shared_draft("does-not-exist", self.store)

    def test_history_required_when_store_missing(self):
        with self.assertRaises(HistoryRequiredError):
            get_shared_draft("anything", None)


class CommentServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.drafts = DraftStore(self.tmp.name)
        self.store = CommentStore(self.tmp.name, self.drafts)
        self.draft_id = self.drafts.save("R1", "# R1", 1, 0)["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_list_and_resolve(self):
        result = add_draft_comment(
            self.draft_id, "REL-1", "hello", "Ada", None, self.store
        )
        self.assertEqual(result["comment"]["id"], "c1")
        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["summary"]["unresolved"], 1)

        listing = list_draft_comments(self.draft_id, self.store)
        self.assertEqual(len(listing["comments"]), 1)
        self.assertEqual(listing["comments"][0]["author"], "Ada")

        resolved = resolve_draft_comment(
            self.draft_id, "c1", True, self.store
        )
        self.assertTrue(resolved["comment"]["resolved"])
        self.assertEqual(resolved["summary"]["unresolved"], 0)

    def test_add_unknown_draft_raises(self):
        with self.assertRaises(LookupError):
            add_draft_comment("missing", "REL-1", "hi", "Ada", None, self.store)

    def test_list_unknown_draft_raises(self):
        with self.assertRaises(LookupError):
            list_draft_comments("missing", self.store)

    def test_resolve_unknown_comment_raises(self):
        add_draft_comment(self.draft_id, "REL-1", "hi", "Ada", None, self.store)
        with self.assertRaises(LookupError):
            resolve_draft_comment(self.draft_id, "c99", True, self.store)

    def test_missing_draft_id_raises(self):
        with self.assertRaises(ValueError):
            add_draft_comment("", "REL-1", "hi", "Ada", None, self.store)

    def test_build_draft_includes_comments_summary(self):
        add_draft_comment(self.draft_id, "REL-1", "hi", "Ada", None, self.store)
        payload = build_draft(
            sample("csv")["content"],
            "csv",
            draft_id=self.draft_id,
            comment_store=self.store,
        )
        self.assertIn("comments", payload)
        self.assertEqual(payload["comments"]["total"], 1)
        self.assertIn("entries", payload["comments"])

    def test_build_draft_without_draft_id_has_no_comments(self):
        payload = build_draft(sample("csv")["content"], "csv")
        self.assertNotIn("comments", payload)


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
