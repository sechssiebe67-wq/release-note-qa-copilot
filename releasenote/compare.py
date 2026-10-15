"""Draft-to-draft comparison matrix.

The pairwise diff answers "what changed since the last release?" and the trend
answers "is quality getting better or worse?", but neither answers "which of
these N candidate drafts is the most publishable, and what is unique to each?".
This module compares **2–10** complete draft payloads deterministically and
offline.

Each ``content`` string is parsed with :func:`releasenote.importer.parse_entries`
and QA-checked with :func:`releasenote.qa.analyze_entries` under the resolved
config, so a draft scores exactly as it does through ``POST /api/generate``.

The result is a pure, JSON-serialisable mapping::

    {
      "drafts": [
        {"label": ..., "entry_count": ..., "counts": {...},
         "publishable": ..., "sourced_ratio": ...,
         "vague_hits": ..., "banned_hits": ...}
      ],
      "matrix": [
        {"key": ..., "id": ..., "title": ..., "type": ...,
         "presence": {label: "identical"|"changed"|"unique"}}
      ],
      "shared": [entry keys present in every draft],
      "unique": {label: [entry keys only in that draft]},
      "recommendation": {"label": ..., "reason": ...}
    }

Only the Python standard library is used.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

from .config import WorkspaceConfig
from .importer import parse_entries
from .models import ChangeEntry
from .profiles import resolve_config
from .qa import analyze_entries
from .trend import _counts, _quality_key, _sourced_ratio

MIN_DRAFTS = 2
MAX_DRAFTS = 10

STATUS_IDENTICAL = "identical"
STATUS_CHANGED = "changed"
STATUS_UNIQUE = "unique"

ConfigInput = Optional[Union[WorkspaceConfig, Dict[str, Any]]]


def _normalize(text: Any) -> str:
    """Lower-case and collapse whitespace so trivial edits do not count."""
    return " ".join(str(text or "").split()).lower()


def _coerce_workspace(config: ConfigInput) -> WorkspaceConfig:
    """Resolve ``config``/``profile`` into a validated workspace config.

    A :class:`WorkspaceConfig` is used as-is. A mapping that carries a
    ``profile`` or ``config`` key is resolved by
    :func:`releasenote.profiles.resolve_config` directly (so an unknown profile
    propagates); any other mapping is treated as the inline config.
    """
    if isinstance(config, WorkspaceConfig):
        return config
    if config is None:
        return resolve_config(None)
    if not isinstance(config, dict):
        raise ValueError("config must be a WorkspaceConfig or a mapping")
    if "profile" in config or "config" in config:
        return resolve_config(config)
    return resolve_config({"config": config})


def _normalize_drafts(drafts: Any) -> List[Dict[str, str]]:
    """Validate and normalise the draft list.

    Raises :class:`ValueError` for a non-list, a count outside
    ``MIN_DRAFTS``..``MAX_DRAFTS``, a non-object item, or an item with empty
    ``content``. A missing/blank label becomes ``Draft N``.
    """
    if not isinstance(drafts, list):
        raise ValueError("'drafts' must be a list of 2–10 draft objects")
    if len(drafts) < MIN_DRAFTS or len(drafts) > MAX_DRAFTS:
        raise ValueError(
            "'drafts' must contain between {} and {} drafts".format(
                MIN_DRAFTS, MAX_DRAFTS
            )
        )
    normalized: List[Dict[str, str]] = []
    for index, item in enumerate(drafts):
        position = index + 1
        if not isinstance(item, dict):
            raise ValueError("draft {} must be an object".format(position))
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(
                "draft {} requires non-empty 'content'".format(position)
            )
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            label = "Draft {}".format(position)
        fmt = item.get("format", "auto")
        if not isinstance(fmt, str):
            raise ValueError(
                "draft {} 'format' must be a string".format(position)
            )
        normalized.append(
            {"label": label.strip(), "content": content, "format": fmt}
        )
    return normalized


def _entry_key(entry: ChangeEntry) -> str:
    """Return the matrix key for ``entry`` (normalized id, then title)."""
    normalized_id = _normalize(entry.id)
    if normalized_id:
        return normalized_id
    return _normalize(entry.title)


def _entry_signature(entry: ChangeEntry) -> Tuple[str, ...]:
    """Return the comparable fields that decide ``identical`` vs ``changed``."""
    return (
        entry.id,
        entry.type,
        entry.title,
        entry.description,
        entry.benefit,
        entry.source,
        entry.link,
        entry.audience,
    )


def _metric_row(label: str, entries: List[ChangeEntry], qa: Any) -> Dict[str, Any]:
    """Build the per-draft metrics row shared with the trend table."""
    return {
        "label": label,
        "entry_count": len(entries),
        "counts": _counts(qa),
        "publishable": qa.publishable,
        "sourced_ratio": _sourced_ratio(entries),
        "vague_hits": sum(
            1 for flag in qa.flags if flag.code == "vague_language"
        ),
        "banned_hits": sum(
            1 for flag in qa.flags if flag.code == "banned_phrase"
        ),
    }


def _recommend(drafts: List[Dict[str, Any]]) -> Dict[str, str]:
    """Pick the most publishable draft using the deterministic rule.

    Fewer ``high`` flags, then fewer ``total`` flags, then a higher
    ``sourced_ratio``; ties fall back to input order (``min`` is stable).
    """
    best = min(drafts, key=_quality_key)
    reason = (
        "Fewest high-severity flags ({high}), fewest total flags ({total}) "
        "and highest sourced ratio ({ratio:.2f})."
    ).format(
        high=best["counts"]["high"],
        total=best["counts"]["total"],
        ratio=best["sourced_ratio"],
    )
    return {"label": best["label"], "reason": reason}


def compute_comparison(
    drafts: Any,
    config: ConfigInput = None,
) -> Dict[str, Any]:
    """Compare 2–10 complete drafts and return a comparison report.

    ``drafts`` is a list of ``{label, content, format}`` mappings. Each content
    string is parsed with :func:`releasenote.importer.parse_entries` and
    QA-checked with :func:`releasenote.qa.analyze_entries` under the resolved
    ``config``/``profile``.

    Raises :class:`ValueError` for an invalid draft list/config (including an
    unknown profile via :func:`releasenote.profiles.resolve_config`) and
    :class:`releasenote.importer.ParseError` for unparseable content.
    """
    workspace = _coerce_workspace(config)
    normalized = _normalize_drafts(drafts)

    draft_rows: List[Dict[str, Any]] = []
    parsed: List[Tuple[str, List[ChangeEntry]]] = []
    for item in normalized:
        entries = parse_entries(item["content"], item["format"])
        qa = analyze_entries(entries, workspace)
        draft_rows.append(_metric_row(item["label"], entries, qa))
        parsed.append((item["label"], entries))

    labels = [row["label"] for row in draft_rows]

    order: List[str] = []
    entries_by_key: Dict[str, Dict[str, ChangeEntry]] = {}
    reference: Dict[str, ChangeEntry] = {}
    for label, entries in parsed:
        for entry in entries:
            key = _entry_key(entry)
            if not key:
                continue
            if key not in entries_by_key:
                entries_by_key[key] = {}
                reference[key] = entry
                order.append(key)
            entries_by_key[key].setdefault(label, entry)

    matrix: List[Dict[str, Any]] = []
    shared: List[str] = []
    unique: Dict[str, List[str]] = {label: [] for label in labels}
    for key in order:
        per_label = entries_by_key[key]
        containing = [label for label in labels if label in per_label]
        first = reference[key]
        presence: Dict[str, str] = {}
        for label in containing:
            if len(containing) == 1:
                presence[label] = STATUS_UNIQUE
            elif _entry_signature(per_label[label]) == _entry_signature(first):
                presence[label] = STATUS_IDENTICAL
            else:
                presence[label] = STATUS_CHANGED
        matrix.append(
            {
                "key": key,
                "id": first.id,
                "title": first.title,
                "type": first.type,
                "presence": presence,
            }
        )
        if len(containing) == len(labels):
            shared.append(key)
        if len(containing) == 1:
            unique[containing[0]].append(key)

    return {
        "drafts": draft_rows,
        "matrix": matrix,
        "shared": shared,
        "unique": unique,
        "recommendation": _recommend(draft_rows),
    }


__all__ = [
    "MIN_DRAFTS",
    "MAX_DRAFTS",
    "STATUS_IDENTICAL",
    "STATUS_CHANGED",
    "STATUS_UNIQUE",
    "compute_comparison",
]
