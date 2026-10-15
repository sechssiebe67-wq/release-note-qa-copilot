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
from .diff import diff_drafts, compute_diff
from .exporters import checklist_csv, diff_csv, draft_json, qa_flags_csv

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
    "diff_drafts",
    "compute_diff",
    "qa_flags_csv",
    "checklist_csv",
    "diff_csv",
    "draft_json",
]

__version__ = "0.4.0"
