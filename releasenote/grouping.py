"""Group imported entries into the sections used by a release note."""

from __future__ import annotations

from typing import Dict, List

from .models import (
    GROUP_ORDER,
    GROUP_TITLES,
    TYPE_OTHER,
    ChangeEntry,
)


def group_entries(entries: List[ChangeEntry]) -> Dict[str, List[ChangeEntry]]:
    """Return entries bucketed by canonical type, preserving input order.

    All known groups are present even when empty, and unknown types fall back
    to the ``other`` bucket.
    """
    groups: Dict[str, List[ChangeEntry]] = {key: [] for key in GROUP_ORDER}
    for entry in entries:
        group = entry.type if entry.type in groups else TYPE_OTHER
        groups[group].append(entry)
    return groups


def group_titles() -> Dict[str, str]:
    """Expose the display titles for each group."""
    return dict(GROUP_TITLES)
