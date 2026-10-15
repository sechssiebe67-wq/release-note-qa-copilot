"""Parity between the browser trend downloads and the Python exporters.

The *Download trend CSV* / *Download trend JSON* buttons build their output in
the browser from the payload returned by ``POST /api/trend``. Python renders
booleans as ``True``/``False`` and integral floats as ``1.0``/``0.0`` while
JavaScript renders them as ``true``/``false`` and ``1``/``0``, so the browser
helpers must normalise those two cell types to stay byte-compatible with
``GET /api/export/trend.csv`` / ``.json``.

This test extracts the pure helpers from ``releasenote/static/index.html``,
runs them with Node against real trend reports and compares the result with
:func:`releasenote.exporters.trend_csv` / :func:`trend_json`.
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

from releasenote.exporters import trend_csv, trend_json  # noqa: E402
from releasenote.service import build_trend  # noqa: E402

_HELPERS = (
    "csvCell",
    "buildCsv",
    "pyBool",
    "pyFloat",
    "stableJson",
    "buildTrendCsv",
    "buildTrendJson",
)

# A sourced entry so ratios can be integral (1.0) or fractional (0.666...).
_SOURCED = (
    "id,type,title,description,benefit,source\n"
    'REL-1,feature,"Title, with comma",Clear benefit,Good,REL-1\n'
    'REL-2,feature,Second entry,"Quoted ""detail""",Better,\n'
    "REL-3,feature,Third entry,Plain detail,Best,REL-3\n"
)
_UNSOURCED = (
    "id,type,title,description,benefit,source\n"
    'REL-1,feature,First entry,Vague stuff,Just better,\n'
    "REL-2,feature,Second entry,More things,Very robust,\n"
    "REL-3,feature,Third entry,Some changes,Easy,\n"
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


def _release(label, content):
    return {"label": label, "content": content, "format": "csv"}


def _reports():
    return {
        "integral_ratios": build_trend(
            {"releases": [_release("2026.1", _SOURCED), _release("2026.2", _SOURCED)]}
        ),
        "fractional_ratios": build_trend(
            {"releases": [_release("r1", _SOURCED), _release('café, "q"', _UNSOURCED)]}
        ),
        "mixed_with_profile": build_trend(
            {
                "releases": [
                    _release("r1", _UNSOURCED),
                    _release("r2", _SOURCED),
                    _release("r3", _UNSOURCED),
                ],
                "profile": "strict",
            }
        ),
    }


@unittest.skipUnless(shutil.which("node"), "node is required to run the UI helpers")
class UiTrendExportParityTests(unittest.TestCase):
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
                actual = self._run("buildTrendCsv(report)", report)
                expected = trend_csv(report)
                self.assertEqual(actual, expected)

    def test_json_matches_python_exporter(self):
        for name, report in _reports().items():
            with self.subTest(report=name):
                actual = self._run("buildTrendJson(report)", report)
                expected = trend_json(report)
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
