"""Pure helpers for the file-backed draft archive.

The archive layers search, filtering and free-form tags on top of the existing
``DATA_DIR`` draft history: reviewers can find a past draft again once it has
scrolled out of the flat history list, and organise drafts into coherent sets
such as ``2.0 launch`` or ``security patch``.

Everything in this module is pure and deterministic. The storage layer
(:class:`releasenote.storage.ArchiveStore`) owns the append-only tag log and
:mod:`releasenote.service` wires the two together.

Only the Python standard library is used.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# A tag is a short, lower-case, slug-like label.
MAX_TAG_LENGTH = 40
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Pagination defaults for the archive listing.
DEFAULT_LIMIT = 50
MAX_LIMIT = 500

# Filter values accepted by ``status`` and ``band``.
ARCHIVE_STATUSES = ("pending", "approved", "rejected")
ARCHIVE_BANDS = ("ready", "review", "risky")

TAG_ADD = "add"
TAG_REMOVE = "remove"
TAG_ACTIONS = (TAG_ADD, TAG_REMOVE)


def normalize_tag(tag: Any) -> str:
    """Return the canonical (trimmed, lower-cased) form of ``tag``.

    Raises :class:`ValueError` when ``tag`` is not a string. Empty strings are
    returned unchanged so callers can decide how to treat them.
    """
    if not isinstance(tag, str):
        raise ValueError("tag must be a string")
    return tag.strip().lower()


def validate_tag(tag: Any) -> str:
    """Validate ``tag`` and return its canonical form.

    A valid tag is non-empty after trimming, lower-cased, at most
    :data:`MAX_TAG_LENGTH` characters and matches
    ``[a-z0-9][a-z0-9_-]*``. Raises :class:`ValueError` otherwise.
    """
    normalized = normalize_tag(tag)
    if not normalized:
        raise ValueError("tag must not be empty")
    if len(normalized) > MAX_TAG_LENGTH:
        raise ValueError(
            "tag must be at most {} characters".format(MAX_TAG_LENGTH)
        )
    if not TAG_PATTERN.match(normalized):
        raise ValueError(
            "tag must start with a letter or digit and contain only "
            "a-z, 0-9, '-' or '_'"
        )
    return normalized


def _parse_datetime(value: Any) -> datetime:
    """Parse an ISO-8601 timestamp into an aware UTC ``datetime``."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected an ISO-8601 timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("invalid ISO-8601 timestamp: {!r}".format(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _clamp_limit(limit: Any) -> int:
    """Return a bounded page size (default :data:`DEFAULT_LIMIT`)."""
    if limit is None:
        return DEFAULT_LIMIT
    try:
        value = int(limit)
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer")
    if value < 0:
        raise ValueError("limit must be >= 0")
    return min(value, MAX_LIMIT)


def _clamp_offset(offset: Any) -> int:
    """Return a non-negative page offset (default ``0``)."""
    if offset is None:
        return 0
    try:
        value = int(offset)
    except (TypeError, ValueError):
        raise ValueError("offset must be an integer")
    return max(0, value)


def _matches_query(record: Dict[str, Any], query: str) -> bool:
    """Case-insensitive substring match on the title and any entry title."""
    needle = query.strip().lower()
    if not needle:
        return True
    title = record.get("title")
    if isinstance(title, str) and needle in title.lower():
        return True
    for entry_title in record.get("entry_titles") or []:
        if isinstance(entry_title, str) and needle in entry_title.lower():
            return True
    return False


def _within_window(
    record: Dict[str, Any],
    since: Optional[datetime],
    until: Optional[datetime],
) -> bool:
    """Whether the record's ``created_at`` falls inside the window (inclusive)."""
    try:
        created = _parse_datetime(record.get("created_at"))
    except ValueError:
        return False
    if since is not None and created < since:
        return False
    if until is not None and created > until:
        return False
    return True


def filter_drafts(
    records: Any,
    query: Optional[str] = None,
    tag: Optional[str] = None,
    status: Optional[str] = None,
    band: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: Any = DEFAULT_LIMIT,
    offset: Any = 0,
) -> Dict[str, Any]:
    """Filter and paginate archive entries into a stable listing.

    ``records`` is a list of archive-entry mappings, each carrying ``draft_id``,
    ``title``, ``created_at``, ``status``, ``band``, ``entry_count`` and
    ``tags``; an optional ``entry_titles`` list is used for the ``query`` match
    and stripped from the result.

    The listing is ordered by ``created_at`` descending with ties broken by
    ``draft_id`` ascending. ``query`` matches case-insensitively on the draft
    title and on any entry title. ``tag``/``status``/``band`` filter on exact
    values. ``since``/``until`` are inclusive ISO-8601 bounds. ``limit``
    defaults to :data:`DEFAULT_LIMIT` and is capped at :data:`MAX_LIMIT`;
    ``offset`` is non-negative.

    Raises :class:`ValueError` for an invalid tag, an unknown status or band or
    a non-integer limit/offset. The result is::

        {"drafts": [...], "total": N, "limit": L, "offset": O}
    """
    if records is None:
        records = []
    if not isinstance(records, list):
        raise ValueError("records must be a list")

    page_limit = _clamp_limit(limit)
    page_offset = _clamp_offset(offset)

    tag_filter: Optional[str] = None
    if tag is not None and str(tag).strip():
        tag_filter = validate_tag(tag)

    status_filter: Optional[str] = None
    if status is not None and str(status).strip():
        status_filter = str(status).strip().lower()
        if status_filter not in ARCHIVE_STATUSES:
            raise ValueError("unknown status: {!r}".format(status))

    band_filter: Optional[str] = None
    if band is not None and str(band).strip():
        band_filter = str(band).strip().lower()
        if band_filter not in ARCHIVE_BANDS:
            raise ValueError("unknown band: {!r}".format(band))

    since_dt = _parse_datetime(since) if since else None
    until_dt = _parse_datetime(until) if until else None

    filtered: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if query and not _matches_query(record, str(query)):
            continue
        if tag_filter is not None and tag_filter not in (record.get("tags") or []):
            continue
        if status_filter is not None and record.get("status") != status_filter:
            continue
        if band_filter is not None and record.get("band") != band_filter:
            continue
        if (since_dt is not None or until_dt is not None) and not _within_window(
            record, since_dt, until_dt
        ):
            continue
        filtered.append(record)

    # Stable two-pass sort: created_at descending, ties by draft_id ascending.
    filtered.sort(key=lambda item: str(item.get("draft_id") or ""))
    filtered.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)

    total = len(filtered)
    page = filtered[page_offset : page_offset + page_limit]
    drafts = [
        {key: value for key, value in record.items() if key != "entry_titles"}
        for record in page
    ]
    return {
        "drafts": drafts,
        "total": total,
        "limit": page_limit,
        "offset": page_offset,
    }


__all__ = [
    "MAX_TAG_LENGTH",
    "TAG_PATTERN",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "ARCHIVE_STATUSES",
    "ARCHIVE_BANDS",
    "TAG_ADD",
    "TAG_REMOVE",
    "TAG_ACTIONS",
    "normalize_tag",
    "validate_tag",
    "filter_drafts",
]
