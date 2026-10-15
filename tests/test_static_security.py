"""Guards against DOM XSS in the browser UI (releasenote/static/index.html).

Untrusted changelog/ticket fields (titles, descriptions, sources, ids, ...)
are inserted into the page with ``innerHTML``. This test makes sure every such
value is passed through the ``esc`` HTML-encoding helper and that the helper
itself neutralizes markup.
"""

import json
import os
import re
import shutil
import subprocess
import unittest

_INDEX = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "releasenote",
    "static",
    "index.html",
)

# Fields that ultimately come from user-supplied CSV/JSON or the generate title.
UNTRUSTED = (
    "e.title",
    "e.description",
    "e.source",
    "e.link",
    "e.id",
    "e.type",
    "f.message",
    "f.code",
    "f.severity",
    "item.detail",
    "item.label",
    "d.title",
)

_ESC_DEF_RE = re.compile(
    r"(const ESCAPE_MAP = \{.*?\};\n\s*const esc = .*?;\n)",
    re.DOTALL,
)


def _load_script():
    with open(_INDEX, encoding="utf-8") as handle:
        html = handle.read()
    match = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    assert match, "index.html must contain a <script> block"
    return match.group(1)


class StaticXssTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = _load_script()

    def test_escape_helper_is_defined(self):
        self.assertRegex(self.script, r"const esc = ")

    def test_untrusted_fields_are_not_concatenated_raw(self):
        # Before the fix values were inserted as `'...' + e.title + '...'`;
        # now they always appear as `esc(e.title ...)`, so a bare `+ field`
        # (or `+ (field`) indicates an unescaped interpolation.
        for field in UNTRUSTED:
            for pattern in (
                r"\+\s*" + re.escape(field) + r"\b",
                r"\+\s*\(\s*" + re.escape(field) + r"\b",
            ):
                self.assertIsNone(
                    re.search(pattern, self.script),
                    "{field} is concatenated without esc()".format(field=field),
                )

    def test_untrusted_fields_are_escaped(self):
        # Each field must be interpolated into the UI and passed through esc()
        # (e.g. esc(e.source || e.link || "none")). Non-HTML uses such as the
        # byId[e.id] lookup are ignored.
        for field in UNTRUSTED:
            occurrences = list(re.finditer(re.escape(field), self.script))
            self.assertTrue(occurrences, "{field} is unused".format(field=field))
            self.assertTrue(
                any(self._inside_esc(match.start()) for match in occurrences),
                "{field} is never escaped".format(field=field),
            )

    def _inside_esc(self, index):
        """True if the code position sits inside an unclosed esc(...) call."""
        depth = 0
        cursor = index
        while cursor > 0:
            cursor -= 1
            char = self.script[cursor]
            if char == ")":
                depth += 1
            elif char == "(":
                if depth == 0:
                    return self.script[max(0, cursor - 3):cursor] == "esc"
                depth -= 1
        return False


@unittest.skipUnless(shutil.which("node"), "node is required to execute the UI helper")
class EscapeHelperBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        match = _ESC_DEF_RE.search(_load_script())
        assert match, "could not locate the esc helper definition"
        cls.helper = match.group(1)

    def _esc(self, payload):
        code = (
            self.helper
            + "\nconst payload = " + json.dumps(payload) + ";\n"
            + "process.stdout.write(esc(payload));\n"
        )
        result = subprocess.run(
            ["node", "-e", code], capture_output=True, text=True, check=True
        )
        return result.stdout

    def test_escapes_html_metacharacters(self):
        self.assertEqual(
            self._esc('<img src=x onerror=alert(document.domain)>'),
            "&lt;img src=x onerror=alert(document.domain)&gt;",
        )

    def test_escapes_quotes_and_ampersand(self):
        self.assertEqual(self._esc("&\"'<>"), "&amp;&quot;&#39;&lt;&gt;")

    def test_escapes_non_string_values(self):
        self.assertEqual(self._esc(42), "42")


if __name__ == "__main__":
    unittest.main()
