"""Parity between the browser score downloads and the Python exporters.

The *Download score CSV* / *Download score JSON* buttons build their output in
the browser from the ``score`` object returned by ``POST /api/generate`` /
``POST /api/diff``. Python renders integral floats as ``30.0`` while JavaScript
renders them as ``30``, so the browser helpers normalise the ``points`` field to
stay byte-compatible with ``GET /api/export/score.csv`` / ``.json``.

This test extracts the pure helpers from ``releasenote/static/index.html`` and
runs them with Node against real score objects.
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

from releasenote.exporters import score_csv, score_json  # noqa: E402
from releasenote.service import build_diff, build_draft  # noqa: E402

_HELPERS = (
    "csvCell",
    "buildCsv",
    "pyFloat",
    "stableJson",
    "buildScoreCsv",
    "buildScoreJson",
)

_VAGUE = (
    "id,type,title,description,benefit,source\n"
    "A,fix,Better sync,The sync is improved,A much better experience,A\n"
)
_CLEAN = (
    "id,type,title,description,benefit,source\n"
    "A,fix,Fix login,Login no longer fails,Reliable access,A\n"
)
_PREVIOUS = (
    "id,type,title,description,benefit,source,link\n"
    "A,feature,Alpha,Alpha does things,Fast and reliable,SRC-A,https://a\n"
)
_CURRENT = (
    "id,type,title,description,benefit,source,link\n"
    "A,feature,Alpha,Alpha does things,Reliable and fast,,\n"
)


def _load_script():
    with open(_INDEX, encoding="utf-8") as handle:
        return handle.read()


def _extract_function(script, name):
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


def _scores():
    return {
        "clean": build_draft(_CLEAN, "csv")["score"],
        "high_penalty": build_draft(
            _VAGUE,
            "csv",
            config={"severity_overrides": {"vague_language": "high"}},
        )["score"],
        "gated": build_draft(
            _VAGUE, "csv", config={"min_score": 95, "max_high": 0}
        )["score"],
        "diff": build_diff(_PREVIOUS, _CURRENT, "csv")["score"],
    }


@unittest.skipUnless(shutil.which("node"), "node is required to run the UI helpers")
class ScoreUiExportParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = _load_script()
        cls.helpers = "\n".join(
            _extract_function(script, name) for name in _HELPERS
        )

    def _run(self, expression, score):
        code = (
            self.helpers
            + "\nconst score = " + json.dumps(score) + ";\n"
            + "process.stdout.write(" + expression + ");\n"
        )
        result = subprocess.run(
            ["node", "-e", code], capture_output=True, text=True, check=True
        )
        return result.stdout

    def test_csv_matches_python_exporter(self):
        for name, score in _scores().items():
            with self.subTest(score=name):
                payload = {"score": score}
                actual = self._run("buildScoreCsv(score)", score)
                self.assertEqual(actual, score_csv(payload))

    def test_json_matches_python_exporter(self):
        for name, score in _scores().items():
            with self.subTest(score=name):
                payload = {"score": score}
                actual = self._run("buildScoreJson(score)", score)
                self.assertEqual(actual, score_json(payload))


if __name__ == "__main__":
    unittest.main()
