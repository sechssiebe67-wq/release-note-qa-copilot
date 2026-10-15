"""High level pipeline that turns raw text into a QA-annotated draft."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from .compare import compute_comparison
from .config import WorkspaceConfig, sample_config
from .diff import compute_diff
from .generator import render_markdown
from .grouping import group_entries, group_titles
from .importer import parse_entries, sample_csv, sample_json
from .models import (
    REVIEW_PENDING,
    REVIEW_STATUSES,
    TYPE_OTHER,
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
from .templates import (
    ReleaseTemplate,
    default_template,
    resolve_template,
    template_from_dict,
    template_summary,
)
from .trend import compute_trend

ConfigInput = Optional[Union[WorkspaceConfig, Dict[str, Any]]]


class HistoryRequiredError(RuntimeError):
    """Raised when a share link is requested but ``DATA_DIR`` is unset.

    Share links read a stored draft, so they are only available when the
    file-backed history store is enabled. The server maps this to HTTP 400.
    """


def _entry_from_dict(data: Dict[str, Any]) -> ChangeEntry:
    """Reconstruct a :class:`ChangeEntry` from a stored entry mapping."""
    return ChangeEntry(
        id=str(data.get("id") or ""),
        type=str(data.get("type") or TYPE_OTHER),
        title=str(data.get("title") or ""),
        description=str(data.get("description") or ""),
        benefit=str(data.get("benefit") or ""),
        source=str(data.get("source") or ""),
        link=str(data.get("link") or ""),
        audience=str(data.get("audience") or ""),
    )


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
    template: Optional[ReleaseTemplate] = None,
) -> Dict[str, Any]:
    """Build the standard draft payload shared by generate and diff."""
    template = template or default_template()
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
        "template": template_summary(template),
        "rules": rule_summary(entries, qa, workspace, template),
        "markdown": render_markdown(title, entries, qa, template),
    }


def _coerce_template(
    template: Optional[Union[ReleaseTemplate, str, Dict[str, Any]]]
) -> ReleaseTemplate:
    """Normalise a template name / object / ``None`` into a template.

    Raises :class:`releasenote.templates.UnknownTemplateError` for an unknown
    name and :class:`ValueError` for an invalid inline template.
    """
    if template is None:
        return default_template()
    if isinstance(template, ReleaseTemplate):
        return template
    return resolve_template(template)


def build_draft(
    content: str,
    fmt: str = "auto",
    title: str = "Release Notes",
    config: ConfigInput = None,
    profile: Optional[str] = None,
    template: Optional[Union[ReleaseTemplate, str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Parse ``content`` and return draft, groups and QA data.

    ``config`` is an optional workspace config mapping (or a
    :class:`~releasenote.config.WorkspaceConfig`). It is validated and its
    effective form is echoed back under the ``config`` key. ``profile`` is the
    optional resolved profile name, echoed back under ``profile``. ``template``
    is an optional template name, object or :class:`ReleaseTemplate`; the
    resolved template is echoed back under ``template``.

    Raises :class:`releasenote.importer.ParseError` for invalid input,
    :class:`ValueError` for an invalid config or template and
    :class:`releasenote.templates.UnknownTemplateError` for an unknown template.
    """
    workspace = _coerce_config(config)
    resolved_template = _coerce_template(template)
    entries = parse_entries(content, fmt)
    qa = analyze_entries(entries, workspace, resolved_template)
    return _assemble(
        entries, qa, workspace, title, profile, resolved_template
    )


def build_diff(
    previous_content: str,
    current_content: str,
    fmt: str = "auto",
    title: str = "Release Notes",
    config: ConfigInput = None,
    profile: Optional[str] = None,
    template: Optional[Union[ReleaseTemplate, str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Compare a previous and a current draft and annotate the current one.

    Both inputs are parsed with the same ``fmt`` rules as
    :func:`build_draft`. The standard draft payload is returned with the
    diff flags merged into ``qa`` and a ``diff`` key holding the classified
    entries, repeated claims and regressions.

    Raises :class:`releasenote.importer.ParseError` for invalid input,
    :class:`ValueError` for an invalid config or template and
    :class:`releasenote.templates.UnknownTemplateError` for an unknown template.
    """
    workspace = _coerce_config(config)
    resolved_template = _coerce_template(template)
    previous = parse_entries(previous_content, fmt)
    current = parse_entries(current_content, fmt)
    diff, diff_flags = compute_diff(previous, current, workspace)
    qa = analyze_entries(current, workspace, resolved_template)
    qa.flags.extend(diff_flags)
    payload = _assemble(
        current, qa, workspace, title, profile, resolved_template
    )
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


def build_comparison(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate ``payload`` and return a draft comparison matrix.

    ``payload`` carries a ``drafts`` list of 2–10 ``{label, content, format}``
    objects plus the optional ``profile`` and ``config`` fields. The resolved
    config and the resolved profile name are echoed back alongside the report.

    Raises :class:`ValueError` for a malformed payload, a draft count outside
    2–10, empty content or an invalid config,
    :class:`releasenote.profiles.UnknownProfileError` for an unknown profile
    and :class:`releasenote.importer.ParseError` for unparseable content.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    drafts = payload.get("drafts")
    workspace = resolve_config(payload)
    profile_name = normalize_profile_name(payload.get("profile"))
    report = compute_comparison(drafts, workspace)
    report["config"] = workspace.to_dict()
    report["profile"] = profile_name
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


def sample_comparison() -> Dict[str, Any]:
    """Return a two-draft example payload for ``GET /api/compare/sample``.

    ``branch-a`` and ``branch-b`` share one identical entry, branch a has one
    unique entry, branch b has one unique entry and ``REL-1`` is ``changed``
    between the two, so the sample exercises every presence state.
    """
    return {
        "drafts": [
            {
                "label": "branch-a",
                "format": "csv",
                "content": (
                    "id,type,title,description,benefit,source\n"
                    "REL-1,feature,Bulk export,Export all reports in one click,"
                    "Saves time per report,REL-1\n"
                    "REL-2,fix,Timezone fix,Timestamps render locally,"
                    "Correct local times,REL-2\n"
                    "REL-4,fix,Retry logic,Retries transient failures,"
                    "Stable uploads,REL-4\n"
                ),
            },
            {
                "label": "branch-b",
                "format": "csv",
                "content": (
                    "id,type,title,description,benefit,source\n"
                    "REL-1,feature,Bulk export,Export all reports in one click,"
                    "One-click export saves time,REL-1\n"
                    "REL-3,feature,Search filters,Filter results by team,"
                    "Relevant results only,REL-3\n"
                    "REL-4,fix,Retry logic,Retries transient failures,"
                    "Stable uploads,REL-4\n"
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


def get_shared_draft(
    draft_id: str,
    store: Optional[Any] = None,
    review_store: Optional[ReviewStore] = None,
) -> Dict[str, Any]:
    """Return a read-only, JSON-serialisable view of a stored draft.

    The stored entries are re-analysed with the **current** heuristics and the
    workspace config that produced the draft, so editing
    :mod:`releasenote.qa` changes the share output without regenerating the
    draft. The current review status is merged in. The payload has the keys
    ``draft_id``, ``title``, ``markdown``, ``qa``, ``checklist``, ``rules``,
    ``review`` and ``created_at``.

    Raises :class:`HistoryRequiredError` when no history store is available
    (``DATA_DIR`` unset) and :class:`LookupError` for an unknown draft id.
    """
    if store is None:
        raise HistoryRequiredError(
            "share links require history; set DATA_DIR to enable it"
        )
    record = store.get(draft_id)
    if record is None:
        raise LookupError("unknown draft id: {}".format(draft_id))

    entries = [
        _entry_from_dict(item)
        for item in (record.get("entries") or [])
        if isinstance(item, dict)
    ]
    config_data = record.get("config")
    workspace = (
        WorkspaceConfig.from_dict(config_data)
        if isinstance(config_data, dict)
        else WorkspaceConfig.default()
    )
    try:
        template = template_from_dict(record.get("template"))
    except ValueError:
        template = default_template()
    qa = analyze_entries(entries, workspace, template)

    review_store = review_store if review_store is not None else ReviewStore()
    review_record = review_store.get(draft_id)
    if review_record is None:
        review = {
            "status": REVIEW_PENDING,
            "reviewer": "",
            "comment": "",
            "updated_at": "",
        }
    else:
        review = {
            "status": review_record.status,
            "reviewer": review_record.reviewer,
            "comment": review_record.comment,
            "updated_at": review_record.updated_at,
        }

    return {
        "draft_id": draft_id,
        "title": record.get("title", ""),
        "markdown": record.get("markdown", ""),
        "qa": qa.to_dict(),
        "checklist": checklist(entries, qa),
        "rules": rule_summary(entries, qa, workspace, template),
        "template": template_summary(template),
        "review": review,
        "created_at": record.get("created_at", ""),
    }
