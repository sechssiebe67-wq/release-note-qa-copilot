"""High level pipeline that turns raw text into a QA-annotated draft."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from .config import WorkspaceConfig, sample_config
from .generator import render_markdown
from .grouping import group_entries, group_titles
from .importer import parse_entries, sample_csv, sample_json
from .models import ChangeEntry
from .qa import analyze_entries, checklist

ConfigInput = Optional[Union[WorkspaceConfig, Dict[str, Any]]]


def _coerce_config(config: ConfigInput) -> WorkspaceConfig:
    """Normalise a config mapping / object / ``None`` into a config.

    Raises :class:`ValueError` when the payload fails validation.
    """
    if config is None:
        return WorkspaceConfig.default()
    if isinstance(config, WorkspaceConfig):
        return config
    return WorkspaceConfig.from_dict(config)


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
    config: ConfigInput = None,
) -> Dict[str, Any]:
    """Parse ``content`` and return draft, groups and QA data.

    ``config`` is an optional workspace config mapping (or a
    :class:`~releasenote.config.WorkspaceConfig`). It is validated and its
    effective form is echoed back under the ``config`` key.

    Raises :class:`releasenote.importer.ParseError` for invalid input and
    :class:`ValueError` for an invalid config.
    """
    workspace = _coerce_config(config)
    entries = parse_entries(content, fmt)
    qa = analyze_entries(entries, workspace)
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
        "config": workspace.to_dict(),
        "markdown": render_markdown(title, entries, qa),
    }


def sample(fmt: str = "csv") -> Dict[str, str]:
    """Return example content for the UI and API."""
    fmt = (fmt or "csv").strip().lower()
    if fmt == "json":
        return {"format": "json", "content": sample_json()}
    return {"format": "csv", "content": sample_csv()}


def sample_workspace_config() -> Dict[str, Any]:
    """Return the documented example workspace config for the API/UI."""
    return sample_config()
