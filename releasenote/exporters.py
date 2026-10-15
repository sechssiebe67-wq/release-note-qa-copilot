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
from typing import Any, Iterable, List, Sequence

QA_FLAG_HEADER = ("entry_id", "code", "severity", "message")
CHECKLIST_HEADER = ("label", "status", "detail")
DIFF_HEADER = ("category", "id", "title", "type", "reason")

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


def draft_json(payload: Mapping[str, Any]) -> str:
    """Serialise the full payload as stable, UTF-8 JSON without NaN values."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )


__all__ = [
    "QA_FLAG_HEADER",
    "CHECKLIST_HEADER",
    "DIFF_HEADER",
    "DIFF_CATEGORIES",
    "qa_flags_csv",
    "checklist_csv",
    "diff_csv",
    "draft_json",
]
