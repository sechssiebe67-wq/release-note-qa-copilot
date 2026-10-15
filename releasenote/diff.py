"""Compare two release drafts and flag regressions and repeated claims.

The diff works entirely offline on entries already understood by the app:
it classifies every current entry as new, changed or unchanged, finds claims
that were reused across releases and detects entries whose quality regressed
(a lost source, a benefit that became vague, or an entry that used to be
publishable but now carries a high-severity flag).

Only the Python standard library is used, so the feature stays
Kubernetes-friendly and dependency-free.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import WorkspaceConfig
from .importer import normalize_type
from .models import (
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    ChangeEntry,
    Flag,
)
from .qa import _with_override, analyze_entry, vague_terms_in

EntryLike = Any


def _normalize(text: Any) -> str:
    """Lower-case and collapse whitespace so trivial edits do not count."""
    return " ".join(str(text or "").split()).lower()


def _entry_from_mapping(data: Dict[str, Any]) -> ChangeEntry:
    """Best-effort conversion of a parsed-draft entry dict to ChangeEntry."""
    return ChangeEntry(
        id=str(data.get("id", "") or "").strip(),
        type=normalize_type(data.get("type")),
        title=str(data.get("title", "") or "").strip(),
        description=str(data.get("description", "") or "").strip(),
        benefit=str(data.get("benefit", "") or "").strip(),
        source=str(data.get("source", "") or "").strip(),
        link=str(data.get("link", "") or "").strip(),
        audience=str(data.get("audience", "") or "").strip(),
    )


def _coerce_entries(value: EntryLike) -> List[ChangeEntry]:
    """Accept a list of entries or a parsed draft payload."""
    if value is None:
        return []
    if isinstance(value, dict):
        value = value.get("entries", [])
    if not isinstance(value, (list, tuple)):
        raise ValueError("expected a list of ChangeEntry or a parsed draft")
    entries: List[ChangeEntry] = []
    for item in value:
        if isinstance(item, ChangeEntry):
            entries.append(item)
        elif isinstance(item, dict):
            entries.append(_entry_from_mapping(item))
        else:
            raise ValueError(
                "each entry must be a ChangeEntry or a mapping"
            )
    return entries


def _item(entry: ChangeEntry, reason: str) -> Dict[str, str]:
    return {
        "id": entry.id,
        "title": entry.title,
        "type": entry.type,
        "reason": reason,
    }


def _flag(
    code: str,
    severity: str,
    message: str,
    entry_id: str,
    config: WorkspaceConfig,
) -> Flag:
    return _with_override(
        Flag(code=code, severity=severity, message=message, entry_id=entry_id),
        config,
    )


def _changed_fields(previous: ChangeEntry, current: ChangeEntry) -> List[str]:
    """Return the entry fields that differ (order stable for readability)."""
    fields: List[str] = []
    if _normalize(previous.title) != _normalize(current.title):
        fields.append("title")
    if previous.description.strip() != current.description.strip():
        fields.append("description")
    if previous.benefit.strip() != current.benefit.strip():
        fields.append("benefit")
    if previous.source.strip() != current.source.strip():
        fields.append("source")
    if previous.link.strip() != current.link.strip():
        fields.append("link")
    if previous.type != current.type:
        fields.append("type")
    if previous.audience.strip() != current.audience.strip():
        fields.append("audience")
    return fields


def _index(entries: Sequence[ChangeEntry]) -> Tuple[Dict[str, int], Dict[str, int]]:
    by_id: Dict[str, int] = {}
    by_title: Dict[str, int] = {}
    for position, entry in enumerate(entries):
        key = _normalize(entry.id)
        if key and key not in by_id:
            by_id[key] = position
        title = _normalize(entry.title)
        if title and title not in by_title:
            by_title[title] = position
    return by_id, by_title


def compute_diff(
    previous: EntryLike,
    current: EntryLike,
    config: Optional[WorkspaceConfig] = None,
) -> Tuple[Dict[str, Any], List[Flag]]:
    """Diff two drafts and return ``(diff, flags)``.

    ``diff`` is a JSON-serialisable mapping with the keys ``added``,
    ``changed``, ``removed``, ``unchanged``, ``repeated_claims`` and
    ``regressions``. ``flags`` are the new QA findings to merge into the
    current draft's :class:`~releasenote.models.QAResult`.
    """
    config = config or WorkspaceConfig.default()
    previous_entries = _coerce_entries(previous)
    current_entries = _coerce_entries(current)

    by_id, by_title = _index(previous_entries)
    used: set = set()

    added: List[Dict[str, str]] = []
    changed: List[Dict[str, str]] = []
    unchanged: List[Dict[str, str]] = []
    repeated: List[Dict[str, str]] = []
    regressions: List[Dict[str, str]] = []
    flags: List[Flag] = []

    previous_benefits = {
        _normalize(entry.benefit)
        for entry in previous_entries
        if _normalize(entry.benefit)
    }

    regression_reasons: Dict[str, List[str]] = {}
    regression_order: List[str] = []
    regression_meta: Dict[str, ChangeEntry] = {}

    def add_regression(entry: ChangeEntry, reason: str) -> None:
        if entry.id not in regression_reasons:
            regression_reasons[entry.id] = []
            regression_order.append(entry.id)
            regression_meta[entry.id] = entry
        if reason not in regression_reasons[entry.id]:
            regression_reasons[entry.id].append(reason)

    matches: List[Tuple[ChangeEntry, Optional[ChangeEntry]]] = []
    for entry in current_entries:
        position: Optional[int] = None
        key = _normalize(entry.id)
        if key and key in by_id and by_id[key] not in used:
            position = by_id[key]
        else:
            title = _normalize(entry.title)
            if title and title in by_title and by_title[title] not in used:
                position = by_title[title]
        match = previous_entries[position] if position is not None else None
        if position is not None:
            used.add(position)
        matches.append((entry, match))

    for entry, match in matches:
        if match is None:
            added.append(_item(entry, "New entry not present in the previous release."))
            continue

        fields = _changed_fields(match, entry)
        if fields:
            changed.append(
                _item(entry, "Changed fields: {}.".format(", ".join(fields)))
            )
        else:
            unchanged.append(_item(entry, "Identical to the previous release."))

        # -- repeated claims ------------------------------------------
        repeat_reasons: List[str] = []
        normalized_benefit = _normalize(entry.benefit)
        if normalized_benefit and normalized_benefit in previous_benefits:
            repeat_reasons.append(
                "Benefit text reused from the previous release."
            )
        if (
            _normalize(entry.title)
            and _normalize(entry.title) == _normalize(match.title)
            and entry.description.strip() == match.description.strip()
        ):
            repeat_reasons.append(
                "Title and description unchanged from the previous release."
            )
        if repeat_reasons:
            message = " ".join(repeat_reasons)
            repeated.append(_item(entry, message))
            flags.append(
                _flag(
                    "repeated_claim",
                    SEVERITY_MEDIUM,
                    message,
                    entry.id,
                    config,
                )
            )

        # -- regressions ----------------------------------------------
        previous_sourced = bool(match.source.strip() or match.link.strip())
        current_sourced = bool(entry.source.strip() or entry.link.strip())
        if previous_sourced and not current_sourced:
            reason = (
                "Previously sourced entry no longer has a source or link."
            )
            add_regression(entry, reason)
            flags.append(
                _flag(
                    "regression_unsourced",
                    SEVERITY_HIGH,
                    reason,
                    entry.id,
                    config,
                )
            )

        previous_benefit = match.benefit.strip()
        current_vague = vague_terms_in(entry.benefit, config)
        if (
            previous_benefit
            and not vague_terms_in(match.benefit, config)
            and current_vague
        ):
            reason = "Benefit became vague: {}.".format(
                ", ".join(current_vague)
            )
            add_regression(entry, reason)
            flags.append(
                _flag(
                    "regression_vague_benefit",
                    SEVERITY_MEDIUM,
                    reason,
                    entry.id,
                    config,
                )
            )

        previous_high = [
            flag
            for flag in analyze_entry(match, config)
            if flag.severity == SEVERITY_HIGH
        ]
        current_high = [
            flag
            for flag in analyze_entry(entry, config)
            if flag.severity == SEVERITY_HIGH
        ]
        if not previous_high and current_high:
            codes = sorted({flag.code for flag in current_high})
            add_regression(
                entry,
                "Previously publishable entry now has high-severity "
                "flag(s): {}.".format(", ".join(codes)),
            )

    removed = [
        _item(entry, "Entry missing from the current release.")
        for position, entry in enumerate(previous_entries)
        if position not in used
    ]

    for entry_id in regression_order:
        regressions.append(
            _item(
                regression_meta[entry_id],
                " ".join(regression_reasons[entry_id]),
            )
        )

    diff: Dict[str, Any] = {
        "added": added,
        "changed": changed,
        "removed": removed,
        "unchanged": unchanged,
        "repeated_claims": repeated,
        "regressions": regressions,
    }
    return diff, flags


def diff_drafts(
    previous: EntryLike,
    current: EntryLike,
    config: Optional[WorkspaceConfig] = None,
) -> Dict[str, Any]:
    """Return the JSON-serialisable diff between two drafts.

    See :func:`compute_diff` for the shape of the result and for access to
    the new QA flags.
    """
    diff, _flags = compute_diff(previous, current, config)
    return diff
