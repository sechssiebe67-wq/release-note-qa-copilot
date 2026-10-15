"""Parity between the browser language downloads and the Python exporters.

The *Download language CSV* / *Download language JSON* buttons build their
output in the browser from the ``language`` object returned by
``POST /api/generate`` / ``POST /api/diff``. Python renders integral floats as
``1.0`` while JavaScript renders them as ``1``, so the browser helpers normalise
the readability float fields to stay byte-compatible with
``GET /api/export/language.csv`` / ``.json``.

This test extracts the pure helpers from ``releasenote/static/index.html`` and
runs them with Node against real language objects.
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

from releasenote.exporters import language_csv, language_json  # noqa: E402
from releasenote.service import build_diff, build_draft, sample_language  # noqa: E402

_HELPERS = (
    "csvCell",
    "buildCsv",
    "pyFloat",
    "stableJson",
    "buildLanguageCsv",
    "buildLanguageJson",
)

_LANGUAGE_SAMPLE = sample_language()
_ACCEPTANCE = (
    "id,type,title,description,benefit,source\n"
    "A,fix,SHIP IT,We are excited to ship this.,"
    "We leverage the platform and save time,A\n"
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


def _payloads():
    return {
        "language_sample": build_draft(
            _LANGUAGE_SAMPLE["content"], _LANGUAGE_SAMPLE["format"]
        ),
        "acceptance": build_draft(_ACCEPTANCE, "csv"),
        "diff": build_diff(_ACCEPTANCE, _ACCEPTANCE, "csv"),
    }


@unittest.skipUnless(shutil.which("node"), "node is required to run the UI helpers")
class LanguageUiExportParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = _load_script()
        cls.helpers = "\n".join(
            _extract_function(script, name) for name in _HELPERS
        )

    def _run(self, expression, language):
        code = (
            self.helpers
            + "\nconst language = " + json.dumps(language) + ";\n"
            + "process.stdout.write(" + expression + ");\n"
        )
        result = subprocess.run(
            ["node", "-e", code], capture_output=True, text=True, check=True
        )
        return result.stdout

    def test_csv_matches_python_exporter(self):
        for name, payload in _payloads().items():
            with self.subTest(payload=name):
                actual = self._run("buildLanguageCsv(language)", payload["language"])
                self.assertEqual(actual, language_csv(payload))

    def test_json_matches_python_exporter(self):
        for name, payload in _payloads().items():
            with self.subTest(payload=name):
                actual = self._run("buildLanguageJson(language)", payload["language"])
                self.assertEqual(actual, language_json(payload))


if __name__ == "__main__":
    unittest.main()
