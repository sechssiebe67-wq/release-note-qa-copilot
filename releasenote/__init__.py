"""Release Note QA Copilot.

Turns raw changelog / ticket exports into a reviewable release-note draft
that links every core statement to a source and flags vague or unsupported
claims before publishing.
"""

from .models import (
    ChangeEntry,
    Flag,
    QAResult,
    ReviewRecord,
    GROUP_TITLES,
    GROUP_ORDER,
    REVIEW_PENDING,
    REVIEW_APPROVED,
    REVIEW_REJECTED,
    REVIEW_STATUSES,
)
from .config import WorkspaceConfig
from .profiles import (
    BUILTIN_PROFILES,
    RuleProfile,
    UnknownProfileError,
    get_profile,
    list_profiles,
    resolve_config,
    rule_summary,
)
from .importer import parse_entries, parse_csv, parse_json, ParseError
from .grouping import group_entries
from .qa import analyze_entries, analyze_entry, effective_vague_terms
from .generator import render_markdown, render_qa_checklist
from .diff import diff_drafts, compute_diff
from .trend import compute_trend
from .exporters import (
    checklist_csv,
    diff_csv,
    draft_json,
    qa_flags_csv,
    rules_csv,
    rules_json,
    trend_csv,
    trend_json,
)
from .storage import DraftStore, ReviewStore
from .service import (
    HistoryRequiredError,
    build_trend,
    get_review,
    get_shared_draft,
    record_review,
)

__all__ = [
    "ChangeEntry",
    "Flag",
    "QAResult",
    "ReviewRecord",
    "GROUP_TITLES",
    "GROUP_ORDER",
    "REVIEW_PENDING",
    "REVIEW_APPROVED",
    "REVIEW_REJECTED",
    "REVIEW_STATUSES",
    "WorkspaceConfig",
    "RuleProfile",
    "UnknownProfileError",
    "BUILTIN_PROFILES",
    "get_profile",
    "list_profiles",
    "resolve_config",
    "rule_summary",
    "parse_entries",
    "parse_csv",
    "parse_json",
    "ParseError",
    "group_entries",
    "analyze_entries",
    "analyze_entry",
    "effective_vague_terms",
    "render_markdown",
    "render_qa_checklist",
    "diff_drafts",
    "compute_diff",
    "compute_trend",
    "build_trend",
    "qa_flags_csv",
    "checklist_csv",
    "diff_csv",
    "rules_csv",
    "rules_json",
    "trend_csv",
    "trend_json",
    "draft_json",
    "DraftStore",
    "ReviewStore",
    "record_review",
    "get_review",
    "get_shared_draft",
    "HistoryRequiredError",
]

__version__ = "0.8.0"
