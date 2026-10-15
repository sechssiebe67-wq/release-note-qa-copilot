"""Parity between the browser profile-comparison downloads and the exporters.

The *Download profile comparison CSV* / *Download profile comparison JSON*
buttons build their output in the browser from the payload returned by
``POST /api/compare-profiles``. Python renders booleans as ``True``/``False``
and integral floats as ``1.0``/``0.0`` while JavaScript renders them as
``true``/``false`` and ``1``/``0``, so the browser helpers must normalise those
two cell types — and sort JSON keys — to stay byte-compatible with
``GET /api/export/profile-comparison.csv`` / ``.json``.

This test extracts the pure helpers from ``releasenote/static/index.html``, runs
them with Node against real comparison reports and compares the result with
:func:`releasenote.exporters.profile_comparison_csv` /
:func:`profile_comparison_json`.
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

from releasenote.exporters import (  # noqa: E402
    profile_comparison_csv,
    profile_comparison_json,
)
from releasenote.service import build_profile_comparison  # noqa: E402

_HELPERS = (
    "csvCell",
    "buildCsv",
    "pyBool",
    "pyFloat",
    "stableJson",
    "buildProfileComparisonCsv",
    "buildProfileComparisonJson",
)

# A sourced entry so ratios can be integral (1.0) or fractional.
_SOURCED = (
    "id,type,title,description,benefit,source\n"
    'REL-1,feature,"Title, with comma",Clear benefit,Good,REL-1\n'
    'REL-2,feature,Second entry,"Quoted ""detail""",Better,\n'
    "REL-3,feature,Third entry,Plain detail,Best,REL-3\n"
)
_HYPE = (
    "id,type,title,description,benefit,source\n"
    "REL-1,feature,Bulk export,Export all reports in one click,"
    "The world's best export experience,REL-1\n"
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


def _reports():
    return {
        "all_builtins": build_profile_comparison(
            {"format": "csv", "content": _SOURCED}
        ),
        "subset_with_hype": build_profile_comparison(
            {
                "format": "csv",
                "content": _HYPE,
                "profiles": ["marketing-safe", "internal"],
            }
        ),
        "with_inline_config": build_profile_comparison(
            {
                "format": "csv",
                "content": _SOURCED,
                "profiles": ["internal"],
                "config": {"banned_phrases": ["best"]},
            }
        ),
    }


@unittest.skipUnless(shutil.which("node"), "node is required to run the UI helpers")
class UiProfileCompareExportParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = _load_script()
        cls.helpers = "\n".join(
            _extract_function(script, name) for name in _HELPERS
        )

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
                actual = self._run("buildProfileComparisonCsv(report)", report)
                expected = profile_comparison_csv(report)
                self.assertEqual(actual, expected)

    def test_json_matches_python_exporter(self):
        for name, report in _reports().items():
            with self.subTest(report=name):
                actual = self._run("buildProfileComparisonJson(report)", report)
                expected = profile_comparison_json(report)
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
