"""Render draft and diff payloads as CSV or JSON for download and automation.

Every function is pure: it takes the payload mapping returned by
:func:`releasenote.service.build_draft` (or ``build_diff``) and returns a
``str``. No I/O is performed, which keeps the exporters easy to test and safe
to use from both the HTTP API and the browser UI.

CSV output is produced with :mod:`csv` so fields containing commas, quotes or
newlines are quoted correctly, and uses ``\\n`` line endings for portability.
Only the Python standard library is used.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from typing import Any, Dict, Iterable, List, Sequence

from .templates import default_template

QA_FLAG_HEADER = ("entry_id", "code", "severity", "message")
CHECKLIST_HEADER = ("label", "status", "detail")
DIFF_HEADER = ("category", "id", "title", "type", "reason")
RULES_HEADER = ("code", "severity", "count")
TEMPLATE_HEADER = ("section", "title", "required", "entry_count")
TREND_HEADER = (
    "label",
    "entry_count",
    "high",
    "medium",
    "low",
    "total",
    "publishable",
    "sourced_ratio",
    "vague_hits",
    "banned_hits",
)

# The comparison per-draft table reuses the trend schema so spreadsheets keep
# one shape; the matrix adds the per-draft presence columns after these.
COMPARISON_HEADER = TREND_HEADER
COMPARISON_MATRIX_HEADER = ("key", "id", "title", "type")

# The profile comparison table repeats the same metrics keyed by profile name.
PROFILE_COMPARISON_HEADER = (
    "profile",
    "entry_count",
    "high",
    "medium",
    "low",
    "total",
    "publishable",
    "sourced_ratio",
    "vague_hits",
    "banned_hits",
)

# Canonical order of the diff categories, matching the server payload.
DIFF_CATEGORIES = (
    "added",
    "changed",
    "removed",
    "unchanged",
    "repeated_claims",
    "regressions",
)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return ``value`` when it is a mapping, else an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    """Return ``value`` when it is a list, else an empty list."""
    return list(value) if isinstance(value, list) else []


def _render_csv(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """Render a header plus rows as RFC-4180 CSV with ``\\n`` line endings."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(list(header))
    for row in rows:
        writer.writerow(["" if value is None else str(value) for value in row])
    return buffer.getvalue()


def qa_flags_csv(payload: Mapping[str, Any]) -> str:
    """Return one CSV row per QA flag: ``entry_id,code,severity,message``.

    A payload without a ``qa`` mapping (or without flags) yields just the
    header row rather than raising.
    """
    flags = _as_mapping(_as_mapping(payload).get("qa")).get("flags")
    rows = []
    for flag in _as_list(flags):
        item = _as_mapping(flag)
        rows.append(
            (
                item.get("entry_id", ""),
                item.get("code", ""),
                item.get("severity", ""),
                item.get("message", ""),
            )
        )
    return _render_csv(QA_FLAG_HEADER, rows)


def checklist_csv(payload: Mapping[str, Any]) -> str:
    """Return one CSV row per checklist item: ``label,status,detail``."""
    rows = []
    for item in _as_list(_as_mapping(payload).get("checklist")):
        entry = _as_mapping(item)
        rows.append(
            (
                entry.get("label", ""),
                entry.get("status", ""),
                entry.get("detail", ""),
            )
        )
    return _render_csv(CHECKLIST_HEADER, rows)


def diff_csv(payload: Mapping[str, Any]) -> str:
    """Return one CSV row per diff item: ``category,id,title,type,reason``.

    ``category`` is one of :data:`DIFF_CATEGORIES`. A payload without a
    ``diff`` key returns only the header row.
    """
    diff = _as_mapping(_as_mapping(payload).get("diff"))
    rows: List[Sequence[Any]] = []
    for category in DIFF_CATEGORIES:
        for item in _as_list(diff.get(category)):
            entry = _as_mapping(item)
            rows.append(
                (
                    category,
                    entry.get("id", ""),
                    entry.get("title", ""),
                    entry.get("type", ""),
                    entry.get("reason", ""),
                )
            )
    return _render_csv(DIFF_HEADER, rows)


def rules_csv(payload: Mapping[str, Any]) -> str:
    """Return one CSV row per firing flag code: ``code,severity,count``.

    The counts come from the ``rules`` summary produced by
    :func:`releasenote.profiles.rule_summary`. When no flag fired only the
    header row is returned, so the shape is always stable.
    """
    rules = _as_mapping(_as_mapping(payload).get("rules"))
    counts = _as_mapping(rules.get("flag_counts"))
    severities = _as_mapping(rules.get("flag_severities"))
    rows = [
        (code, severities.get(code, ""), counts.get(code, 0))
        for code in sorted(counts)
    ]
    return _render_csv(RULES_HEADER, rows)


def rules_json(payload: Mapping[str, Any]) -> str:
    """Serialise the ``rules`` summary as stable, UTF-8 JSON without NaN."""
    rules = _as_mapping(_as_mapping(payload).get("rules"))
    return json.dumps(
        rules,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )


def _section_counts(payload: Mapping[str, Any]) -> Dict[str, int]:
    """Map each canonical group key to the number of entries it holds."""
    counts: Dict[str, int] = {}
    for group in _as_list(_as_mapping(payload).get("groups")):
        item = _as_mapping(group)
        key = item.get("key")
        if isinstance(key, str):
            counts[key] = len(_as_list(item.get("entries")))
    return counts


def _template_export(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the resolved template with a per-section ``entry_count``.

    Falls back to the built-in ``default`` template when the payload carries no
    template, so the shape is always stable.
    """
    data = _as_mapping(payload)
    template = _as_mapping(data.get("template")) or default_template().to_dict()
    counts = _section_counts(data)
    sections: List[Dict[str, Any]] = []
    for item in _as_list(template.get("sections")):
        section = _as_mapping(item)
        key = section.get("key", "")
        sections.append(
            {
                "section": key,
                "title": section.get("title", ""),
                "required": bool(section.get("required", False)),
                "entry_count": counts.get(key, 0),
            }
        )
    return {
        "name": template.get("name", ""),
        "description": template.get("description", ""),
        "intro": template.get("intro", ""),
        "outro": template.get("outro", ""),
        "sections": sections,
    }


def template_csv(payload: Mapping[str, Any]) -> str:
    """Return one CSV row per template section.

    Header: ``section,title,required,entry_count``. ``required`` is rendered as
    ``True`` / ``False`` so the CSV round-trips through standard parsers. A
    payload without a template falls back to the built-in ``default``.
    """
    export = _template_export(payload)
    rows = [
        (
            section["section"],
            section["title"],
            section["required"],
            section["entry_count"],
        )
        for section in export["sections"]
    ]
    return _render_csv(TEMPLATE_HEADER, rows)


def template_json(payload: Mapping[str, Any]) -> str:
    """Serialise the resolved template (with entry counts) as stable JSON."""
    return json.dumps(
        _template_export(payload),
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )


def trend_csv(report: Mapping[str, Any]) -> str:
    """Return one CSV row per release: the documented trend header.

    Header: ``label,entry_count,high,medium,low,total,publishable,
    sourced_ratio,vague_hits,banned_hits``. A report without a ``releases``
    list yields just the header row, so the shape is always stable.
    """
    rows = []
    for item in _as_list(_as_mapping(report).get("releases")):
        release = _as_mapping(item)
        counts = _as_mapping(release.get("counts"))
        rows.append(
            (
                release.get("label", ""),
                release.get("entry_count", 0),
                counts.get("high", 0),
                counts.get("medium", 0),
                counts.get("low", 0),
                counts.get("total", 0),
                release.get("publishable", False),
                release.get("sourced_ratio", 0.0),
                release.get("vague_hits", 0),
                release.get("banned_hits", 0),
            )
        )
    return _render_csv(TREND_HEADER, rows)


def trend_json(report: Mapping[str, Any]) -> str:
    """Serialise a trend report as stable, UTF-8 JSON without NaN values."""
    return json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )


def draft_json(payload: Mapping[str, Any]) -> str:
    """Serialise the full payload as stable, UTF-8 JSON without NaN values."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )


def comparison_csv(report: Mapping[str, Any]) -> str:
    """Return one CSV row per draft: the documented trend header.

    Header: ``label,entry_count,high,medium,low,total,publishable,
    sourced_ratio,vague_hits,banned_hits`` — the same schema as
    :func:`trend_csv` so spreadsheets keep one shape. A report without a
    ``drafts`` list yields just the header row.
    """
    rows = []
    for item in _as_list(_as_mapping(report).get("drafts")):
        draft = _as_mapping(item)
        counts = _as_mapping(draft.get("counts"))
        rows.append(
            (
                draft.get("label", ""),
                draft.get("entry_count", 0),
                counts.get("high", 0),
                counts.get("medium", 0),
                counts.get("low", 0),
                counts.get("total", 0),
                draft.get("publishable", False),
                draft.get("sourced_ratio", 0.0),
                draft.get("vague_hits", 0),
                draft.get("banned_hits", 0),
            )
        )
    return _render_csv(COMPARISON_HEADER, rows)


def comparison_matrix_csv(report: Mapping[str, Any]) -> str:
    """Return the presence matrix as CSV.

    Header: ``key,id,title,type,<label1>,<label2>,…`` — one column per draft
    label in input order, one row per matrix entry. Cells hold ``identical`` /
    ``changed`` / ``unique`` or are empty when the entry is absent from that
    draft.
    """
    data = _as_mapping(report)
    labels = [
        _as_mapping(item).get("label", "")
        for item in _as_list(data.get("drafts"))
    ]
    header = list(COMPARISON_MATRIX_HEADER) + labels
    rows = []
    for item in _as_list(data.get("matrix")):
        entry = _as_mapping(item)
        presence = _as_mapping(entry.get("presence"))
        rows.append(
            [
                entry.get("key", ""),
                entry.get("id", ""),
                entry.get("title", ""),
                entry.get("type", ""),
            ]
            + [presence.get(label, "") for label in labels]
        )
    return _render_csv(header, rows)


def comparison_json(report: Mapping[str, Any]) -> str:
    """Serialise a comparison report as stable, UTF-8 JSON without NaN."""
    return json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )


def profile_comparison_csv(report: Mapping[str, Any]) -> str:
    """Return one CSV row per profile: the documented profile-comparison header.

    Header: ``profile,entry_count,high,medium,low,total,publishable,
    sourced_ratio,vague_hits,banned_hits``. A report without a ``profiles``
    list yields just the header row, so the shape is always stable.
    """
    rows = []
    for item in _as_list(_as_mapping(report).get("profiles")):
        profile = _as_mapping(item)
        counts = _as_mapping(profile.get("counts"))
        rows.append(
            (
                profile.get("name", ""),
                profile.get("entry_count", 0),
                counts.get("high", 0),
                counts.get("medium", 0),
                counts.get("low", 0),
                counts.get("total", 0),
                profile.get("publishable", False),
                profile.get("sourced_ratio", 0.0),
                profile.get("vague_hits", 0),
                profile.get("banned_hits", 0),
            )
        )
    return _render_csv(PROFILE_COMPARISON_HEADER, rows)


def profile_comparison_json(report: Mapping[str, Any]) -> str:
    """Serialise a profile comparison report as stable, UTF-8 JSON without NaN."""
    return json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )


__all__ = [
    "QA_FLAG_HEADER",
    "CHECKLIST_HEADER",
    "DIFF_HEADER",
    "RULES_HEADER",
    "TEMPLATE_HEADER",
    "TREND_HEADER",
    "COMPARISON_HEADER",
    "COMPARISON_MATRIX_HEADER",
    "PROFILE_COMPARISON_HEADER",
    "DIFF_CATEGORIES",
    "qa_flags_csv",
    "checklist_csv",
    "diff_csv",
    "rules_csv",
    "rules_json",
    "template_csv",
    "template_json",
    "trend_csv",
    "trend_json",
    "draft_json",
    "comparison_csv",
    "comparison_matrix_csv",
    "comparison_json",
    "profile_comparison_csv",
    "profile_comparison_json",
]
