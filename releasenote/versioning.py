"""Pure helpers for release-note draft versioning.

Draft versioning keeps an **immutable, ordered** history for a logical draft
identified by a caller-supplied ``draft_key``. Every helper here is pure and
dependency free so it can be tested offline; persistence lives in
:mod:`releasenote.storage` and orchestration in :mod:`releasenote.service`.

Only the Python standard library is used.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from .config import WorkspaceConfig
from .diff import compute_diff

# A canonical draft key is a trimmed, lower-case slug that starts with a
# letter or digit: ``release-2.0``, ``security_patch`` and ``v1.2.3`` are all
# valid, while ``2.0 launch`` is not.
DRAFT_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
MAX_DRAFT_KEY_LENGTH = 64


def validate_draft_key(key: Any) -> str:
    """Return the canonical ``draft_key`` or raise :class:`ValueError`.

    The canonical form is the trimmed input. It must be non-empty, at most
    :data:`MAX_DRAFT_KEY_LENGTH` characters and match
    ``[a-z0-9][a-z0-9._-]*``; anything else raises :class:`ValueError` so the
    API can map it to HTTP 400.
    """
    if not isinstance(key, str):
        raise ValueError("draft_key must be a string")
    canonical = key.strip()
    if not canonical:
        raise ValueError("draft_key is required")
    if len(canonical) > MAX_DRAFT_KEY_LENGTH:
        raise ValueError(
            "draft_key must be at most {} characters".format(
                MAX_DRAFT_KEY_LENGTH
            )
        )
    if not DRAFT_KEY_PATTERN.match(canonical):
        raise ValueError("draft_key must match [a-z0-9][a-z0-9._-]*")
    return canonical


def _coerce_version(value: Any) -> int:
    """Return ``value`` as an ``int`` or raise :class:`ValueError`."""
    if isinstance(value, bool):
        raise ValueError("version must be an integer: {!r}".format(value))
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError("version must be an integer: {!r}".format(value))


def _version_key(record: Dict[str, Any]) -> int:
    try:
        return _coerce_version(record.get("version"))
    except ValueError:
        return 0


def next_version(existing_versions: Optional[Iterable[Any]]) -> int:
    """Return the next version number.

    ``1`` when there are no existing versions, otherwise the highest existing
    version plus one. Every value is coerced to an ``int``.
    """
    versions = [_coerce_version(value) for value in (existing_versions or [])]
    if not versions:
        return 1
    return max(versions) + 1


def sort_versions(
    records: Optional[Iterable[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Return ``records`` ordered by ``version`` ascending.

    The sort is stable, so records that share a version keep their input
    order. Non-mapping items are ignored.
    """
    items = [record for record in (records or []) if isinstance(record, dict)]
    return sorted(items, key=_version_key)


def _coerce_config(config: Any) -> WorkspaceConfig:
    if config is None:
        return WorkspaceConfig.default()
    if isinstance(config, WorkspaceConfig):
        return config
    return WorkspaceConfig.from_dict(config)


def diff_versions(
    previous_payload: Optional[Dict[str, Any]],
    current_payload: Optional[Dict[str, Any]],
    config: Any = None,
) -> Dict[str, Any]:
    """Diff two stored version payloads via :func:`releasenote.diff.compute_diff`.

    Only the ``entries`` of each payload are compared, reusing the existing
    classification (added / changed / removed / unchanged / repeated claims /
    regressions). The result is ``{previous_version, current_version, diff,
    flags}`` where ``flags`` is the list of new QA findings as plain mappings.
    """
    previous = previous_payload if isinstance(previous_payload, dict) else {}
    current = current_payload if isinstance(current_payload, dict) else {}
    workspace = _coerce_config(config)
    diff, flags = compute_diff(
        previous.get("entries") or [],
        current.get("entries") or [],
        workspace,
    )
    return {
        "previous_version": previous.get("version"),
        "current_version": current.get("version"),
        "diff": diff,
        "flags": [flag.to_dict() for flag in flags],
    }


def rollback_plan(
    records: Optional[Iterable[Dict[str, Any]]], target_version: Any
) -> Dict[str, Any]:
    """Return the record for ``target_version`` or raise :class:`ValueError`.

    ``ValueError`` is raised both when ``target_version`` is not an integer and
    when no record carries that version, so a failed lookup never returns a
    partial plan.
    """
    target = _coerce_version(target_version)
    for record in sort_versions(records):
        if _version_key(record) == target:
            return record
    raise ValueError("unknown version: {}".format(target_version))


__all__ = [
    "DRAFT_KEY_PATTERN",
    "MAX_DRAFT_KEY_LENGTH",
    "validate_draft_key",
    "next_version",
    "sort_versions",
    "diff_versions",
    "rollback_plan",
]
