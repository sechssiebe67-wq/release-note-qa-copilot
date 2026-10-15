"""High level pipeline that turns raw text into a QA-annotated draft."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from .config import WorkspaceConfig, sample_config
from .diff import compute_diff
from .generator import render_markdown
from .grouping import group_entries, group_titles
from .importer import parse_entries, sample_csv, sample_json
from .models import (
    REVIEW_PENDING,
    REVIEW_STATUSES,
    ChangeEntry,
    QAResult,
    ReviewRecord,
)
from .profiles import (
    normalize_profile_name,
    resolve_config,
    rule_summary,
)
from .qa import analyze_entries, checklist
from .storage import ReviewStore
from .trend import compute_trend

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


def _assemble(
    entries: List[ChangeEntry],
    qa: QAResult,
    workspace: WorkspaceConfig,
    title: str,
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the standard draft payload shared by generate and diff."""
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
        "profile": profile,
        "rules": rule_summary(entries, qa, workspace),
        "markdown": render_markdown(title, entries, qa),
    }


def build_draft(
    content: str,
    fmt: str = "auto",
    title: str = "Release Notes",
    config: ConfigInput = None,
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse ``content`` and return draft, groups and QA data.

    ``config`` is an optional workspace config mapping (or a
    :class:`~releasenote.config.WorkspaceConfig`). It is validated and its
    effective form is echoed back under the ``config`` key. ``profile`` is the
    optional resolved profile name, echoed back under ``profile``.

    Raises :class:`releasenote.importer.ParseError` for invalid input and
    :class:`ValueError` for an invalid config.
    """
    workspace = _coerce_config(config)
    entries = parse_entries(content, fmt)
    qa = analyze_entries(entries, workspace)
    return _assemble(entries, qa, workspace, title, profile)


def build_diff(
    previous_content: str,
    current_content: str,
    fmt: str = "auto",
    title: str = "Release Notes",
    config: ConfigInput = None,
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Compare a previous and a current draft and annotate the current one.

    Both inputs are parsed with the same ``fmt`` rules as
    :func:`build_draft`. The standard draft payload is returned with the
    diff flags merged into ``qa`` and a ``diff`` key holding the classified
    entries, repeated claims and regressions.

    Raises :class:`releasenote.importer.ParseError` for invalid input and
    :class:`ValueError` for an invalid config.
    """
    workspace = _coerce_config(config)
    previous = parse_entries(previous_content, fmt)
    current = parse_entries(current_content, fmt)
    diff, diff_flags = compute_diff(previous, current, workspace)
    qa = analyze_entries(current, workspace)
    qa.flags.extend(diff_flags)
    payload = _assemble(current, qa, workspace, title, profile)
    payload["diff"] = diff
    return payload


def build_trend(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate ``payload`` and return a multi-release trend report.

    ``payload`` carries an ordered ``releases`` list of 2–20
    ``{label, content, format}`` objects plus the optional ``profile`` and
    ``config`` fields. The resolved config, the resolved profile name and the
    effective-rule summary for the last release are echoed back alongside the
    trend itself.

    Raises :class:`ValueError` for a malformed payload, a release count
    outside 2–20, empty content or an invalid config,
    :class:`releasenote.profiles.UnknownProfileError` for an unknown profile
    and :class:`releasenote.importer.ParseError` for unparseable content.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    releases = payload.get("releases")
    workspace = resolve_config(payload)
    profile_name = normalize_profile_name(payload.get("profile"))
    report = compute_trend(releases, workspace, profile_name)

    last = releases[-1]
    fmt = last.get("format", "auto")
    entries = parse_entries(last["content"], fmt)
    qa = analyze_entries(entries, workspace)
    report["config"] = workspace.to_dict()
    report["profile"] = profile_name
    report["rules"] = rule_summary(entries, qa, workspace)
    return report


def sample(fmt: str = "csv") -> Dict[str, str]:
    """Return example content for the UI and API."""
    fmt = (fmt or "csv").strip().lower()
    if fmt == "json":
        return {"format": "json", "content": sample_json()}
    return {"format": "csv", "content": sample_csv()}


def sample_workspace_config() -> Dict[str, Any]:
    """Return the documented example workspace config for the API/UI."""
    return sample_config()


def sample_trend() -> Dict[str, Any]:
    """Return a three-release example payload for ``GET /api/trend/sample``.

    The releases improve from a high number of flags to a clean draft, so the
    sample exercises every verdict (``improving``) and both sparkline series.
    """
    return {
        "releases": [
            {
                "label": "2026.1",
                "format": "csv",
                "content": (
                    "id,type,title,description,benefit,source\n"
                    "REL-1,feature,Bulk export,Export all reports in one click,"
                    "A much better experience,\n"
                    "REL-2,fix,Timezone fix,Timestamps render in local time,"
                    "No more confusing dates,\n"
                ),
            },
            {
                "label": "2026.2",
                "format": "csv",
                "content": (
                    "id,type,title,description,benefit,source\n"
                    "REL-1,feature,Bulk export,Export all reports in one click,"
                    "One-click export saves time,REL-1\n"
                    "REL-2,fix,Timezone fix,Timestamps render in local time,"
                    "Correct local times,REL-2\n"
                    "REL-3,feature,Better search,Search is improved,"
                    "Find things faster,REL-3\n"
                ),
            },
            {
                "label": "2026.3",
                "format": "csv",
                "content": (
                    "id,type,title,description,benefit,source\n"
                    "REL-1,feature,Bulk export,Export all reports in one click,"
                    "One-click export saves time,REL-1\n"
                    "REL-2,fix,Timezone fix,Timestamps render in local time,"
                    "Correct local times,REL-2\n"
                    "REL-3,feature,Search filters,Filter search results by team,"
                    "Relevant results only,REL-3\n"
                ),
            },
        ]
    }


def record_review(
    draft_id: str,
    status: str,
    reviewer: str = "",
    comment: str = "",
    store: Optional[ReviewStore] = None,
) -> ReviewRecord:
    """Record a review transition for ``draft_id`` and return the new state.

    ``status`` must be one of ``pending`` / ``approved`` / ``rejected`` (case
    insensitive) and ``draft_id`` must be known to ``store``. Raises
    :class:`ValueError` for an unknown status or empty draft id and
    :class:`LookupError` for an unknown draft id. A failed call never appends
    to the audit log.
    """
    if not isinstance(draft_id, str) or not draft_id.strip():
        raise ValueError("draft_id is required")
    normalized = (status or "").strip().lower()
    if normalized not in REVIEW_STATUSES:
        raise ValueError("unknown review status: {!r}".format(status))
    store = store if store is not None else ReviewStore()
    if not store.has_draft(draft_id):
        raise LookupError("unknown draft id: {}".format(draft_id))
    return store.record(
        draft_id,
        normalized,
        str(reviewer or ""),
        str(comment or ""),
    )


def get_review(
    draft_id: str, store: Optional[ReviewStore] = None
) -> Dict[str, Any]:
    """Return the current review state of ``draft_id`` plus its audit trail.

    Raises :class:`LookupError` for a draft id the store does not know about.
    A known draft that has never been reviewed reports ``pending`` with an
    empty audit list.
    """
    store = store if store is not None else ReviewStore()
    if not store.has_draft(draft_id):
        raise LookupError("unknown draft id: {}".format(draft_id))
    record = store.get(draft_id)
    if record is None:
        record = ReviewRecord(id=draft_id, status=REVIEW_PENDING)
    payload = record.to_dict()
    payload["draft_id"] = payload.pop("id")
    payload["audit"] = store.audit(draft_id)
    return payload
