"""High level pipeline that turns raw text into a QA-annotated draft."""

from __future__ import annotations

from typing import Any, Dict, List

from .generator import render_markdown
from .grouping import group_entries, group_titles
from .importer import parse_entries, sample_csv, sample_json
from .models import ChangeEntry
from .qa import analyze_entries, checklist


def _entry_dict(entry: ChangeEntry) -> Dict[str, Any]:
    return {
        "id": entry.id,
        "type": entry.type,
        "title": entry.title,
        "description": entry.description,
        "benefit": entry.benefit,
        "source": entry.source,
        "link": entry.link,
        "audience": entry.audience,
    }


def build_draft(
    content: str,
    fmt: str = "auto",
    title: str = "Release Notes",
) -> Dict[str, Any]:
    """Parse ``content`` and return draft, groups and QA data.

    Raises :class:`releasenote.importer.ParseError` for invalid input.
    """
    entries = parse_entries(content, fmt)
    qa = analyze_entries(entries)
    groups = group_entries(entries)
    titles = group_titles()
    return {
        "title": title.strip() or "Release Notes",
        "entry_count": len(entries),
        "entries": [_entry_dict(e) for e in entries],
        "groups": [
            {
                "key": key,
                "title": titles.get(key, key.title()),
                "entries": [_entry_dict(e) for e in groups.get(key, [])],
            }
            for key in groups
        ],
        "qa": qa.to_dict(),
        "checklist": checklist(entries, qa),
        "markdown": render_markdown(title, entries, qa),
    }


def sample(fmt: str = "csv") -> Dict[str, str]:
    """Return example content for the UI and API."""
    fmt = (fmt or "csv").strip().lower()
    if fmt == "json":
        return {"format": "json", "content": sample_json()}
    return {"format": "csv", "content": sample_csv()}
