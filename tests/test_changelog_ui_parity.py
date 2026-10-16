"""Parity between the browser changelog downloads and the Python exporters.

The *Download changelog CSV* / *Download changelog JSON* buttons build their
output in the browser from the payload returned by ``POST /api/changelog``.
Python renders booleans as ``True``/``False`` while JavaScript renders them as
``true``/``false``, so the browser helpers must normalise that cell type to
stay byte-compatible with ``GET /api/export/changelog.csv`` / ``.json``.

This test extracts the pure helpers from ``releasenote/static/index.html``,
runs them with Node against real changelog digests and compares the result with
:func:`releasenote.exporters.changelog_csv` / :func:`changelog_json`.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INDEX = os.path.join(_ROOT, "releasenote", "static", "index.html")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from releasenote.exporters import changelog_csv, changelog_json  # noqa: E402
from releasenote.service import build_changelog  # noqa: E402

_HELPERS = (
    "csvCell",
    "buildCsv",
    "pyBool",
    "pyFloat",
    "stableJson",
    "buildChangelogCsv",
    "buildChangelogJson",
)

_BAD = (
    "id,type,title,description,benefit,source\n"
    'REL-1,feature,"Title, with comma",Export all reports,'
    "A much better experience,\n"
    "REL-2,fix,Timezone fix,Timestamps render in local time,"
    "No more confusing dates,\n"
)
_CLEAN = (
    "id,type,title,description,benefit,source\n"
    "REL-1,feature,Bulk export,Export all reports,"
    "One-click export saves time,REL-1\n"
    "REL-2,fix,Timezone fix,Timestamps render in local time,"
    "Correct local times,REL-2\n"
)


def _load_script():
    with open(_INDEX, encoding="utf-8") as handle:
        return handle.read()


def _extract_function(script, name):
    """Return the source of a top-level ``function name(...) {...}`` block."""
    match = re.search(r"function\s+" + re.escape(name) + r"\s*\(", script)
    assert match, "could not find function {!r}".format(name)
    brace = script.index("{", match.end())
    depth = 0
    for index in range(brace, len(script)):
        char = script[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return script[match.start():index + 1]
    raise AssertionError("unbalanced braces for {!r}".format(name))


def _release(label, content, date=None):
    item = {"label": label, "content": content, "format": "csv"}
    if date is not None:
        item["date"] = date
    return item


def _reports():
    return {
        "mixed": build_changelog(
            [
                _release("2026.1", _BAD, "2026-01-15"),
                _release("2026.2", _CLEAN, "2026-02-15"),
            ]
        ),
        "quoted_labels": build_changelog(
            [
                _release('café, "q"', _BAD),
                _release("plain", _CLEAN),
            ]
        ),
    }


@unittest.skipUnless(shutil.which("node"), "node is required to run the UI helpers")
class UiChangelogExportParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = _load_script()
        cls.helpers = "\n".join(_extract_function(script, name) for name in _HELPERS)

    def _run(self, expression, report):
        code = (
            self.helpers
            + "\nconst report = " + json.dumps(report) + ";\n"
            + "process.stdout.write(" + expression + ");\n"
        )
        result = subprocess.run(
            ["node", "-e", code], capture_output=True, text=True, check=True
        )
        return result.stdout

    def test_csv_matches_python_exporter(self):
        for name, report in _reports().items():
            with self.subTest(report=name):
                actual = self._run("buildChangelogCsv(report)", report)
                expected = changelog_csv(report)
                self.assertEqual(actual, expected)

    def test_json_matches_python_exporter(self):
        for name, report in _reports().items():
            with self.subTest(report=name):
                actual = self._run("buildChangelogJson(report)", report)
                expected = changelog_json(report)
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
