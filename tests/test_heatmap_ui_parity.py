"""Parity between the browser heatmap downloads and the Python exporters.

The *Download heatmap CSV* / *Download heatmap JSON* buttons build their output
in the browser from the heatmap object returned by ``POST /api/heatmap``. This
test extracts the pure helpers from ``releasenote/static/index.html``, runs them
with Node against real heatmaps and compares the result with
:func:`releasenote.exporters.heatmap_csv` / :func:`heatmap_json` so the two stay
byte-identical.
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

from releasenote.exporters import heatmap_csv, heatmap_json  # noqa: E402
from releasenote.service import build_heatmap  # noqa: E402

_HELPERS = (
    "csvCell",
    "buildCsv",
    "pyFloat",
    "stableJson",
    "buildHeatmapCsv",
    "buildHeatmapJson",
)

_BAD = (
    "id,type,title,description,benefit,source\n"
    'REL-1,feature,"Title, with a comma",Export all reports,'
    "A much better experience,\n"
    "REL-2,fix,Timezone fix,Timestamps render in local time,"
    "Correct local times,REL-2\n"
)
_CLEAN = (
    "id,type,title,description,benefit,source\n"
    "REL-1,fix,Fix login,Login no longer fails,Reliable access,REL-1\n"
)
_RISKY = (
    "id,type,title,description,benefit,source\n"
    "REL-9,breaking,Drop the v1 API,The legacy API is removed.,"
    "Migrate before upgrading.,\n"
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


def _heatmaps():
    return {
        "bad": build_heatmap({"content": _BAD, "format": "csv"}),
        "clean": build_heatmap({"content": _CLEAN, "format": "csv"}),
        "strict": build_heatmap(
            {"content": _RISKY, "format": "csv", "profile": "strict"}
        ),
    }


@unittest.skipUnless(shutil.which("node"), "node is required to run the UI helpers")
class HeatmapUiExportParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = _load_script()
        cls.helpers = "\n".join(
            _extract_function(script, name) for name in _HELPERS
        )

    def _run(self, expression, heatmap):
        code = (
            self.helpers
            + "\nconst heatmap = " + json.dumps(heatmap) + ";\n"
            + "process.stdout.write(" + expression + ");\n"
        )
        result = subprocess.run(
            ["node", "-e", code], capture_output=True, text=True, check=True
        )
        return result.stdout

    def test_csv_matches_python_exporter(self):
        for name, heatmap in _heatmaps().items():
            with self.subTest(heatmap=name):
                actual = self._run("buildHeatmapCsv(heatmap)", heatmap)
                self.assertEqual(actual, heatmap_csv(heatmap))

    def test_json_matches_python_exporter(self):
        for name, heatmap in _heatmaps().items():
            with self.subTest(heatmap=name):
                actual = self._run("buildHeatmapJson(heatmap)", heatmap)
                self.assertEqual(actual, heatmap_json(heatmap))


if __name__ == "__main__":
    unittest.main()
