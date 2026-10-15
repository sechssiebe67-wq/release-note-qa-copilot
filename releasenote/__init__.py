"""Release Note QA Copilot.

Turns raw changelog / ticket exports into a reviewable release-note draft
that links every core statement to a source and flags vague or unsupported
claims before publishing.
"""

from .models import ChangeEntry, Flag, QAResult, GROUP_TITLES, GROUP_ORDER
from .config import WorkspaceConfig
from .importer import parse_entries, parse_csv, parse_json, ParseError
from .grouping import group_entries
from .qa import analyze_entries, analyze_entry
from .generator import render_markdown, render_qa_checklist

__all__ = [
    "ChangeEntry",
    "Flag",
    "QAResult",
    "GROUP_TITLES",
    "GROUP_ORDER",
    "WorkspaceConfig",
    "parse_entries",
    "parse_csv",
    "parse_json",
    "ParseError",
    "group_entries",
    "analyze_entries",
    "analyze_entry",
    "render_markdown",
    "render_qa_checklist",
]

__version__ = "0.2.0"
