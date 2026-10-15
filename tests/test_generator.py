import unittest

from releasenote.generator import render_markdown, render_qa_checklist
from releasenote.models import (
    TYPE_BREAKING,
    TYPE_FEATURE,
    TYPE_FIX,
    ChangeEntry,
)
from releasenote.qa import analyze_entries, checklist


class RenderMarkdownTests(unittest.TestCase):
    def _render(self, entries):
        qa = analyze_entries(entries)
        return render_markdown("Release 2.0", entries, qa)

    def test_groups_and_title(self):
        entries = [
            ChangeEntry(
                id="1", type=TYPE_FEATURE, title="Feature A",
                description="Adds A.", benefit="Does A better.", source="1",
            ),
            ChangeEntry(
                id="2", type=TYPE_FIX, title="Fix B",
                description="Fixes B.", source="2",
            ),
        ]
        md = self._render(entries)
        self.assertTrue(md.startswith("# Release 2.0"))
        self.assertIn("## Features", md)
        self.assertIn("## Fixes", md)
        self.assertIn("### Feature A", md)
        self.assertIn("**Customer benefit:** Does A better.", md)
        self.assertIn("**Source:** 1", md)
        self.assertIn("## QA Checklist", md)

    def test_source_with_link(self):
        entries = [
            ChangeEntry(
                id="1", type=TYPE_FIX, title="Fix", description="D",
                source="REL-1", link="https://example.com/REL-1",
            )
        ]
        md = self._render(entries)
        self.assertIn("[REL-1](https://example.com/REL-1)", md)

    def test_missing_source_rendered(self):
        entries = [
            ChangeEntry(id="1", type=TYPE_FIX, title="Fix", description="D")
        ]
        md = self._render(entries)
        self.assertIn("_(none provided)_", md)
        self.assertIn("missing_source", md)

    def test_breaking_group_title(self):
        entries = [
            ChangeEntry(
                id="1", type=TYPE_BREAKING, title="Remove v1",
                description="v1 removed. Migrate to v2.", source="1",
            )
        ]
        md = self._render(entries)
        self.assertIn("## Breaking Changes", md)

    def test_empty_entries(self):
        md = self._render([])
        self.assertIn("# Release 2.0", md)
        self.assertIn("0 entries", md)

    def test_singular_entry_label(self):
        entries = [
            ChangeEntry(
                id="1", type=TYPE_FIX, title="T", description="D", source="1"
            )
        ]
        md = self._render(entries)
        self.assertIn("1 entry ", md)


class RenderChecklistTests(unittest.TestCase):
    def test_renders_status_icons(self):
        items = [
            {"label": "A", "status": "pass", "detail": "ok"},
            {"label": "B", "status": "warn", "detail": "maybe"},
            {"label": "C", "status": "fail", "detail": "no"},
        ]
        lines = render_qa_checklist(items)
        text = "\n".join(lines)
        self.assertIn("\u2705 A", text)
        self.assertIn("\u26a0 B", text)
        self.assertIn("\u274c C", text)


if __name__ == "__main__":
    unittest.main()
