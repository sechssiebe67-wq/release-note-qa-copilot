import csv
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

from releasenote.exporters import (
    TEMPLATE_HEADER,
    template_csv,
    template_json,
)
from releasenote.generator import render_markdown
from releasenote.models import ChangeEntry
from releasenote.qa import analyze_entries
from releasenote.server import create_server
from releasenote.service import build_draft
from releasenote.templates import (
    BUILTIN_TEMPLATES,
    ReleaseTemplate,
    TemplateSection,
    UnknownTemplateError,
    default_template,
    get_template,
    list_templates,
    resolve_template,
    template_from_dict,
    template_summary,
)

FIX_ONLY = (
    "id,type,title,description,benefit,source\n"
    "A,fix,Fix login,Login no longer fails,Reliable access,A\n"
)

MIXED = (
    "id,type,title,description,benefit,source\n"
    "A,feature,Add SSO,SAML login,Central control,A\n"
    "B,fix,Fix login,Login no longer fails,Reliable access,B\n"
    "C,breaking,Remove v1,Legacy endpoints removed,Migrate to v2,C\n"
)


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


class BuiltinTemplateTests(unittest.TestCase):
    def test_expected_templates_present(self):
        for name in ("default", "github-release", "email"):
            self.assertIn(name, BUILTIN_TEMPLATES)

    def test_templates_are_frozen_and_well_formed(self):
        for name, template in BUILTIN_TEMPLATES.items():
            self.assertIsInstance(template, ReleaseTemplate)
            self.assertEqual(template.name, name)
            self.assertTrue(template.description)
            self.assertTrue(template.sections)
            for section in template.sections:
                self.assertIsInstance(section, TemplateSection)

    def test_default_matches_canonical_order(self):
        self.assertEqual(
            default_template().section_keys(),
            ("feature", "fix", "breaking", "other"),
        )

    def test_github_release_puts_breaking_first(self):
        self.assertEqual(
            get_template("github-release").section_keys()[0], "breaking"
        )

    def test_email_leads_with_fixes(self):
        self.assertEqual(get_template("email").section_keys()[0], "fix")

    def test_list_templates_is_json_serialisable(self):
        templates = list_templates()
        self.assertEqual(
            [item["name"] for item in templates], sorted(BUILTIN_TEMPLATES)
        )
        for item in templates:
            self.assertEqual(
                set(item),
                {"name", "description", "sections", "intro", "outro"},
            )
        json.dumps(templates)

    def test_get_template_unknown_raises(self):
        with self.assertRaises(UnknownTemplateError):
            get_template("nope")
        with self.assertRaises(ValueError):
            get_template("  ")

    def test_template_to_dict_round_trips(self):
        for template in BUILTIN_TEMPLATES.values():
            rebuilt = template_from_dict(template.to_dict())
            self.assertEqual(rebuilt, template)


class ResolveTemplateTests(unittest.TestCase):
    def test_none_and_empty_are_default(self):
        self.assertEqual(resolve_template(None), default_template())
        self.assertEqual(resolve_template(""), default_template())

    def test_name_lookup(self):
        self.assertEqual(
            resolve_template("github-release").name, "github-release"
        )

    def test_unknown_name_raises(self):
        with self.assertRaises(UnknownTemplateError):
            resolve_template("nope")

    def test_inline_mapping_layers_over_named_base(self):
        template = resolve_template(
            {
                "name": "github-release",
                "intro": "Custom intro",
                "sections": ["feature", "fix"],
            }
        )
        self.assertEqual(template.name, "github-release")
        self.assertEqual(template.intro, "Custom intro")
        self.assertEqual(template.section_keys(), ("feature", "fix"))
        # The fields not overridden are inherited from the built-in base.
        self.assertEqual(template.outro, get_template("github-release").outro)

    def test_inline_custom_name_over_default(self):
        template = resolve_template(
            {"name": "house", "sections": [{"key": "feature", "required": True}]}
        )
        self.assertEqual(template.name, "house")
        self.assertTrue(template.sections[0].required)

    def test_explicit_inline_over_base_argument(self):
        template = resolve_template("default", {"intro": "Hi"})
        self.assertEqual(template.intro, "Hi")
        self.assertEqual(template.section_keys(), default_template().section_keys())

    def test_unknown_template_key_raises(self):
        with self.assertRaises(ValueError):
            resolve_template({"name": "default", "bogus": 1})

    def test_unknown_section_key_raises(self):
        with self.assertRaises(ValueError):
            resolve_template({"sections": [{"key": "nope"}]})

    def test_non_string_sections_raises(self):
        with self.assertRaises(ValueError):
            resolve_template({"sections": "feature"})

    def test_sections_deduplicated_preserving_order(self):
        template = resolve_template({"sections": ["fix", "feature", "fix"]})
        self.assertEqual(template.section_keys(), ("fix", "feature"))

    def test_template_summary_accepts_raw_values(self):
        summary = template_summary("email")
        self.assertEqual(summary["name"], "email")
        self.assertIsInstance(summary["sections"], list)

    def test_resolve_template_accepts_request_payload(self):
        template = resolve_template(
            {"content": "x", "template": "github-release"}
        )
        self.assertEqual(template.name, "github-release")

    def test_resolve_template_from_payload_without_template_is_default(self):
        from releasenote.templates import resolve_template_from_payload

        self.assertEqual(
            resolve_template_from_payload({"content": "x"}), default_template()
        )


class RenderTemplateTests(unittest.TestCase):
    def _render(self, template, content=FIX_ONLY):
        result = build_draft(content, "csv", template=template)
        return result["markdown"]

    def test_default_matches_no_template(self):
        no_template = build_draft(FIX_ONLY, "csv")
        with_default = build_draft(FIX_ONLY, "csv", template="default")
        self.assertEqual(no_template["markdown"], with_default["markdown"])

    def test_section_order_follows_template(self):
        md = self._render("github-release", MIXED)
        self.assertLess(
            md.index("## Breaking Changes"), md.index("## Features")
        )
        self.assertLess(md.index("## Features"), md.index("## Fixes"))

    def test_intro_and_outro_rendered(self):
        md = self._render("github-release")
        self.assertIn("## What's Changed", md)
        self.assertIn("**Full Changelog**", md)

    def test_email_outro_multiline(self):
        md = self._render("email")
        self.assertIn("Thanks,\nThe Release Team", md)

    def test_required_empty_section_rendered_with_placeholder(self):
        md = self._render("github-release")
        self.assertIn("## Features", md)
        self.assertIn("_(no entries)_", md)

    def test_optional_empty_section_skipped(self):
        md = self._render("email")
        self.assertNotIn("## Breaking Changes", md)

    def test_render_markdown_accepts_optional_template(self):
        entries = [
            ChangeEntry(
                id="1", type="fix", title="Fix", description="D", source="1"
            )
        ]
        qa = analyze_entries(entries)
        md = render_markdown("Release", entries, qa)
        self.assertIn("## Fixes", md)


class TemplateFlagTests(unittest.TestCase):
    def test_required_empty_section_flag(self):
        result = build_draft(FIX_ONLY, "csv", template="github-release")
        flags = [
            f
            for f in result["qa"]["flags"]
            if f["code"] == "missing_template_section"
        ]
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["severity"], "medium")
        self.assertEqual(flags[0]["entry_id"], "")

    def test_no_flag_when_required_section_present(self):
        content = (
            "id,type,title,description,benefit,source\n"
            "A,feature,Add login,SAML login,Central control,A\n"
        )
        result = build_draft(content, "csv", template="github-release")
        codes = {f["code"] for f in result["qa"]["flags"]}
        self.assertNotIn("missing_template_section", codes)

    def test_severity_override_applies(self):
        result = build_draft(
            FIX_ONLY,
            "csv",
            template="github-release",
            config={"severity_overrides": {"missing_template_section": "high"}},
        )
        flag = next(
            f
            for f in result["qa"]["flags"]
            if f["code"] == "missing_template_section"
        )
        self.assertEqual(flag["severity"], "high")
        self.assertFalse(result["qa"]["publishable"])

    def test_no_template_no_flag(self):
        result = build_draft(FIX_ONLY, "csv")
        codes = {f["code"] for f in result["qa"]["flags"]}
        self.assertNotIn("missing_template_section", codes)

    def test_rules_contains_template(self):
        result = build_draft(FIX_ONLY, "csv", template="github-release")
        self.assertEqual(
            result["rules"]["template"]["name"], "github-release"
        )


class TemplateExporterTests(unittest.TestCase):
    def _payload(self, template="github-release"):
        return build_draft(
            "id,type,title,description,benefit,source\n"
            "A,fix,Fix login,Login no longer fails,Reliable access,A\n"
            "B,feature,Add SSO,SAML login,Central control,B\n",
            "csv",
            template=template,
        )

    def test_template_csv_header_and_rows(self):
        rows = _rows(template_csv(self._payload()))
        self.assertEqual(
            rows[0], ["section", "title", "required", "entry_count"]
        )
        template = self._payload()["template"]
        self.assertEqual(len(rows) - 1, len(template["sections"]))
        by_section = {row[0]: row for row in rows[1:]}
        self.assertEqual(by_section["feature"][2], "True")
        self.assertEqual(by_section["feature"][3], "1")
        self.assertEqual(by_section["breaking"][2], "False")
        self.assertEqual(by_section["breaking"][3], "0")

    def test_template_csv_without_template_falls_back_to_default(self):
        rows = _rows(template_csv({}))
        self.assertEqual(rows[0], list(TEMPLATE_HEADER))
        self.assertEqual([row[0] for row in rows[1:]], ["feature", "fix", "breaking", "other"])

    def test_template_json_is_stable(self):
        text = template_json(self._payload())
        decoded = json.loads(text)
        self.assertEqual(decoded["name"], "github-release")
        self.assertEqual(
            text,
            json.dumps(decoded, ensure_ascii=False, sort_keys=True, allow_nan=False),
        )

    def test_template_header_constant(self):
        self.assertEqual(
            TEMPLATE_HEADER, ("section", "title", "required", "entry_count")
        )


class _TemplateServerMixin:
    @classmethod
    def _start(cls):
        cls.server, host, port = create_server("127.0.0.1", 0)
        cls.base = "http://127.0.0.1:{}".format(port)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()

    @classmethod
    def _stop(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def _get(self, path, raw=False):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def _get_json(self, path):
        status, _headers, body = self._get(path, raw=True)
        return status, json.loads(body.decode("utf-8"))

    def _post_status(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


class TemplateServerTests(_TemplateServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._start()

    @classmethod
    def tearDownClass(cls):
        cls._stop()

    def test_templates_endpoint_lists_builtins(self):
        status, payload = self._get_json("/api/templates")
        self.assertEqual(status, 200)
        templates = payload["templates"]
        names = {item["name"] for item in templates}
        self.assertTrue({"default", "github-release", "email"} <= names)
        for item in templates:
            self.assertEqual(
                set(item),
                {"name", "description", "sections", "intro", "outro"},
            )

    def test_generate_with_template(self):
        status, result = self._post_status(
            "/api/generate",
            {"content": MIXED, "format": "csv", "template": "github-release"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["template"]["name"], "github-release")
        self.assertLess(
            result["markdown"].index("## Breaking Changes"),
            result["markdown"].index("## Features"),
        )
        self.assertIn("## What's Changed", result["markdown"])
        self.assertIn("**Full Changelog**", result["markdown"])
        self.assertIn("template", result["rules"])

    def test_generate_inline_template(self):
        inline = {
            "sections": [
                {"key": "feature", "required": True},
                {"key": "fix"},
            ],
            "intro": "Custom intro",
        }
        status, result = self._post_status(
            "/api/generate",
            {"content": FIX_ONLY, "format": "csv", "template": inline},
        )
        self.assertEqual(status, 200)
        self.assertIn("Custom intro", result["markdown"])
        codes = {f["code"] for f in result["qa"]["flags"]}
        self.assertIn("missing_template_section", codes)

    def test_generate_unknown_template_is_400(self):
        status, result = self._post_status(
            "/api/generate",
            {"content": FIX_ONLY, "template": "does-not-exist"},
        )
        self.assertEqual(status, 400)
        self.assertIn("does-not-exist", result["error"])

    def test_generate_invalid_inline_template_is_400(self):
        status, result = self._post_status(
            "/api/generate",
            {"content": FIX_ONLY, "template": {"sections": ["nope"]}},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_generate_unparseable_content_still_422(self):
        status, result = self._post_status(
            "/api/generate",
            {"content": "foo,bar\n1,2\n", "format": "csv", "template": "email"},
        )
        self.assertEqual(status, 422)
        self.assertIn("error", result)

    def test_diff_with_template(self):
        status, result = self._post_status(
            "/api/diff",
            {
                "previous": FIX_ONLY,
                "current": FIX_ONLY,
                "format": "csv",
                "template": "email",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["template"]["name"], "email")
        self.assertIn("Thanks,\nThe Release Team", result["markdown"])

    def test_export_template_csv(self):
        query = urllib.parse.urlencode(
            {"content": FIX_ONLY, "format": "csv", "template": "github-release"}
        )
        status, headers, body = self._get(
            "/api/export/template.csv?" + query, raw=True
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("release-template.csv", headers["Content-Disposition"])
        rows = _rows(body.decode("utf-8"))
        self.assertEqual(
            rows[0], ["section", "title", "required", "entry_count"]
        )

    def test_export_template_json(self):
        query = urllib.parse.urlencode(
            {"content": FIX_ONLY, "format": "csv", "template": "github-release"}
        )
        status, headers, body = self._get(
            "/api/export/template.json?" + query, raw=True
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("attachment", headers["Content-Disposition"])
        decoded = json.loads(body.decode("utf-8"))
        self.assertEqual(decoded["name"], "github-release")
        self.assertEqual(decoded["sections"][0]["section"], "breaking")

    def test_export_template_diff_path(self):
        query = urllib.parse.urlencode(
            {
                "previous": FIX_ONLY,
                "current": FIX_ONLY,
                "format": "csv",
                "template": "email",
            }
        )
        status, _headers, body = self._get(
            "/api/export/template.csv?" + query, raw=True
        )
        self.assertEqual(status, 200)
        rows = _rows(body.decode("utf-8"))
        self.assertEqual([row[0] for row in rows[1:]], ["fix", "feature", "breaking"])

    def test_export_template_unknown_template_is_400(self):
        query = urllib.parse.urlencode(
            {"content": FIX_ONLY, "format": "csv", "template": "nope"}
        )
        status, _headers, body = self._get(
            "/api/export/template.csv?" + query, raw=True
        )
        self.assertEqual(status, 400)
        self.assertIn(b"nope", body)

    def test_export_template_missing_content_is_400(self):
        status, _headers, body = self._get(
            "/api/export/template.csv?format=csv", raw=True
        )
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)


class TemplateHistoryTests(_TemplateServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"DATA_DIR": cls.tmp.name})
        cls.env.start()
        cls._start()

    @classmethod
    def tearDownClass(cls):
        cls._stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def _history(self):
        status, _headers, body = self._get("/api/history", raw=True)
        return json.loads(body.decode("utf-8"))["drafts"]

    def test_unknown_template_stores_no_draft(self):
        before = self._history()
        self._post_status(
            "/api/generate",
            {"content": FIX_ONLY, "format": "csv", "template": "nope"},
        )
        self.assertEqual(len(self._history()), len(before))

    def test_share_view_keeps_template(self):
        status, result = self._post_status(
            "/api/generate",
            {"content": FIX_ONLY, "format": "csv", "template": "github-release"},
        )
        self.assertEqual(status, 200)
        draft_id = result["draft_id"]
        status, _headers, body = self._get("/api/share/" + draft_id, raw=True)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["template"]["name"], "github-release")
        self.assertEqual(
            payload["rules"]["template"]["name"], "github-release"
        )


if __name__ == "__main__":
    unittest.main()
