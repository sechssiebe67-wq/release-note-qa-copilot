"""High level pipeline that turns raw text into a QA-annotated draft."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from .archive import filter_drafts, validate_tag
from .comments import summarize_thread
from .compare import compute_comparison
from .config import WorkspaceConfig, sample_config
from .diff import compute_diff
from .generator import render_markdown
from .grouping import group_entries, group_titles
from .importer import parse_entries, sample_csv, sample_json
from .language import compute_language
from .models import (
    REVIEW_PENDING,
    REVIEW_STATUSES,
    TYPE_OTHER,
    ChangeEntry,
    QAResult,
    ReviewRecord,
)
from .profile_compare import compare_profiles
from .profiles import (
    normalize_profile_name,
    resolve_config,
    rule_summary,
)
from .qa import analyze_entries, checklist
from .score import compute_score
from .storage import (
    ArchiveStore,
    CommentStore,
    DraftStore,
    DraftVersionStore,
    ReviewStore,
)
from .templates import (
    ReleaseTemplate,
    default_template,
    resolve_template,
    template_from_dict,
    template_summary,
)
from .trend import compute_trend
from .versioning import (
    diff_versions,
    next_version,
    rollback_plan,
    validate_draft_key,
)

ConfigInput = Optional[Union[WorkspaceConfig, Dict[str, Any]]]


class HistoryRequiredError(RuntimeError):
    """Raised when a stored-draft feature needs ``DATA_DIR`` but it is unset.

    Share links and the draft archive read stored drafts, so they are only
    available when the file-backed history store is enabled. The server maps
    this to HTTP 400 for share links and HTTP 404 for archive endpoints.
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


def _language_report(
    entries: List[ChangeEntry],
    workspace: WorkspaceConfig,
    template: Optional[ReleaseTemplate],
) -> Dict[str, Any]:
    """Return the full language report for the payload and the score.

    The findings are already folded into ``qa`` by
    :func:`releasenote.qa.analyze_entries`; this only produces the report
    object (readability, style, findings and summary) carried under the
    top-level ``language`` key.
    """
    return compute_language(entries, template, workspace)


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
    language = _language_report(entries, workspace, template)
    groups = group_entries(entries)
    titles = group_titles()
    score = compute_score(qa, entries, workspace, language)
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
        "language": language,
        "score": score,
        "gate": score["gate"],
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
    draft_key: Optional[str] = None,
    version_store: Optional[DraftVersionStore] = None,
    draft_id: Optional[str] = None,
    comment_store: Optional[CommentStore] = None,
) -> Dict[str, Any]:
    """Parse ``content`` and return draft, groups and QA data.

    ``config`` is an optional workspace config mapping (or a
    :class:`~releasenote.config.WorkspaceConfig`). It is validated and its
    effective form is echoed back under the ``config`` key. ``profile`` is the
    optional resolved profile name, echoed back under ``profile``. ``template``
    is an optional template name, object or :class:`ReleaseTemplate`; the
    resolved template is echoed back under ``template``.

    When ``draft_key`` is supplied the draft is treated as a new version of a
    logical draft: the canonical key is echoed back under ``draft_key`` and,
    when a ``version_store`` is given, the next version is assigned and echoed
    back under ``version``. The record itself is appended by the caller once
    the ``draft_id`` is known (see :func:`store_draft_version`), so this
    function stays free of write side effects.

    When both ``draft_id`` and ``comment_store`` are supplied the payload gains
    a top-level ``comments`` summary (total/unresolved counts per entry) for
    that stored draft. Without a ``draft_id`` the payload is unchanged, so the
    default shape is preserved.

    Raises :class:`releasenote.importer.ParseError` for invalid input,
    :class:`ValueError` for an invalid config, template or draft key and
    :class:`releasenote.templates.UnknownTemplateError` for an unknown template.
    """
    workspace = _coerce_config(config)
    resolved_template = _coerce_template(template)
    entries = parse_entries(content, fmt)
    qa = analyze_entries(entries, workspace, resolved_template)
    payload = _assemble(
        entries, qa, workspace, title, profile, resolved_template
    )
    if draft_key is not None:
        canonical = validate_draft_key(draft_key)
        payload["draft_key"] = canonical
        if version_store is not None:
            existing = version_store.list_versions(canonical)
            payload["version"] = next_version(
                [record.get("version") for record in existing]
            )
    if draft_id is not None and comment_store is not None:
        payload["comments"] = summarize_thread(comment_store.thread(draft_id))
    return payload


def store_draft_version(
    draft_key: str,
    draft_id: Optional[str],
    payload: Dict[str, Any],
    version_store: Optional[DraftVersionStore] = None,
) -> Dict[str, Any]:
    """Append one immutable version of ``payload`` and return the record.

    ``version_store`` must be durable history (``DATA_DIR`` set); otherwise
    :class:`HistoryRequiredError` is raised so the API can answer HTTP 409.
    The next version of ``draft_key`` is assigned by the store and echoed back
    onto ``payload`` so the API response and the stored record agree.
    """
    if version_store is None:
        raise HistoryRequiredError(
            "draft versioning requires history; set DATA_DIR to enable it"
        )
    canonical = validate_draft_key(draft_key)
    record = version_store.append(canonical, draft_id, payload)
    payload["draft_key"] = canonical
    payload["version"] = record.get("version")
    return record


def list_draft_versions(
    draft_key: str, version_store: Optional[DraftVersionStore] = None
) -> Dict[str, Any]:
    """Return the version listing for ``draft_key`` in ascending order.

    The result is ``{"draft_key": ..., "versions": [{version, draft_id,
    created_at}]}``. An unknown key yields an empty list. Raises
    :class:`HistoryRequiredError` when history is disabled and
    :class:`ValueError` for a malformed key.
    """
    if version_store is None:
        raise HistoryRequiredError(
            "draft versioning requires history; set DATA_DIR to enable it"
        )
    canonical = validate_draft_key(draft_key)
    records = version_store.list_versions(canonical)
    return {
        "draft_key": canonical,
        "versions": [
            {
                "version": record.get("version"),
                "draft_id": record.get("draft_id"),
                "created_at": record.get("created_at"),
            }
            for record in records
        ],
    }


def _coerce_version(version: Any) -> int:
    """Return an integer version or raise :class:`ValueError`."""
    if isinstance(version, bool):
        raise ValueError("version must be an integer: {!r}".format(version))
    try:
        return int(version)
    except (TypeError, ValueError):
        raise ValueError("version must be an integer: {!r}".format(version))


def get_draft_version(
    draft_key: str,
    version: Any,
    version_store: Optional[DraftVersionStore] = None,
) -> Dict[str, Any]:
    """Return the stored payload for one version.

    Raises :class:`HistoryRequiredError` when history is disabled,
    :class:`ValueError` for a malformed key or version and :class:`LookupError`
    for an unknown version.
    """
    if version_store is None:
        raise HistoryRequiredError(
            "draft versioning requires history; set DATA_DIR to enable it"
        )
    canonical = validate_draft_key(draft_key)
    target = _coerce_version(version)
    record = version_store.get_version(canonical, target)
    if record is None:
        raise LookupError("unknown version: {}".format(version))
    return record.get("payload") or {}


def diff_draft_versions(
    draft_key: str,
    previous_version: Any,
    current_version: Any,
    config: ConfigInput = None,
    version_store: Optional[DraftVersionStore] = None,
) -> Dict[str, Any]:
    """Diff two stored versions of ``draft_key``.

    Reuses :func:`releasenote.versioning.diff_versions` (and therefore
    :func:`releasenote.diff.compute_diff`) on the two versions' stored entries
    and returns ``{previous_version, current_version, diff, flags}``. Raises
    :class:`HistoryRequiredError` when history is disabled,
    :class:`ValueError` for malformed input and :class:`LookupError` for an
    unknown version.
    """
    previous = get_draft_version(draft_key, previous_version, version_store)
    current = get_draft_version(draft_key, current_version, version_store)
    return diff_versions(previous, current, config)


def rollback_draft_version(
    draft_key: str,
    version: Any,
    version_store: Optional[DraftVersionStore] = None,
) -> Dict[str, Any]:
    """Return the target version's payload with ``rolled_back_from`` set.

    ``rolled_back_from`` is the previously latest version of the draft, so a
    caller can show what the rollback moved away from. Raises
    :class:`HistoryRequiredError` when history is disabled, :class:`ValueError`
    for a malformed key or version and :class:`LookupError` when the version
    does not exist.
    """
    if version_store is None:
        raise HistoryRequiredError(
            "draft versioning requires history; set DATA_DIR to enable it"
        )
    canonical = validate_draft_key(draft_key)
    target_version = _coerce_version(version)
    records = version_store.list_versions(canonical)
    try:
        target = rollback_plan(records, target_version)
    except ValueError as exc:
        raise LookupError(str(exc))
    payload = dict(target.get("payload") or {})
    latest = records[-1] if records else None
    payload["rolled_back_from"] = latest.get("version") if latest else None
    return payload


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


def build_profile_comparison(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate ``payload`` and compare one changelog across rule profiles.

    ``payload`` carries a ``content`` string plus the optional ``format``,
    ``profiles`` (a list of profile names; default: every built-in) and
    ``config`` (an inline workspace config layered on top of each profile).

    Raises :class:`ValueError` for a malformed payload, a missing ``content``,
    a non-list ``profiles`` value or an invalid config,
    :class:`releasenote.profiles.UnknownProfileError` for an unknown profile
    and :class:`releasenote.importer.ParseError` for unparseable content.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("field 'content' is required")
    fmt = payload.get("format", "auto")
    if not isinstance(fmt, str):
        raise ValueError("'format' must be a string")
    return compare_profiles(
        content,
        fmt,
        payload.get("profiles"),
        payload.get("config"),
    )


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


def sample_profile_comparison() -> Dict[str, Any]:
    """Return a one-changelog example payload for ``GET /api/compare-profiles/sample``.

    The changelog carries an unsourced benefit with a hype phrase so the
    built-in ``strict``, ``marketing-safe`` and ``internal`` profiles score it
    differently and the recommendation is not trivial.
    """
    return {
        "format": "csv",
        "content": (
            "id,type,title,description,benefit,source\n"
            "REL-1,feature,Bulk export,Export all reports in one click,"
            "A much better experience,\n"
            "REL-2,fix,Timezone fix,Timestamps render in local time,"
            "Correct local times,REL-2\n"
        ),
    }


def sample_language() -> Dict[str, Any]:
    """Return an example payload for ``GET /api/language/sample``.

    It is valid input for ``POST /api/generate`` and deliberately exercises the
    language lint: a 40+ word sentence, passive voice, first-person wording,
    ALL-CAPS, jargon, mixed tense and a duplicate heading.
    """
    long_sentence = (
        "We leverage the platform to export every report for every team across "
        "every workspace in a single click, without any manual effort at all, "
        "and the job is generated by the scheduler so nobody has to remember "
        "to run it ever again."
    )
    return {
        "format": "csv",
        "title": "Language lint sample",
        "content": (
            "id,type,title,description,benefit,source\n"
            "REL-1,feature,Bulk export,\"{long}\",Added one-click export.,REL-1\n"
            'REL-2,feature,Bulk export,"Fixed the export job so it runs for '
            'admins.",SHIP IT: saves time for admins \U0001F680.,REL-2\n'
        ).format(long=long_sentence.replace('"', "'")),
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


def _comment_store(comment_store: Optional[CommentStore]) -> CommentStore:
    """Return ``comment_store`` or a fresh in-memory store."""
    return comment_store if comment_store is not None else CommentStore()


def add_draft_comment(
    draft_id: str,
    entry_id: Any,
    body: Any,
    author: Any = "",
    parent_id: Any = None,
    comment_store: Optional[CommentStore] = None,
    now: Any = None,
) -> Dict[str, Any]:
    """Add one comment to a stored draft and return it with the thread summary.

    The response is ``{"draft_id", "comment", "summary"}``. Raises
    :class:`ValueError` for a missing ``draft_id`` or an invalid entry/body and
    :class:`LookupError` for an unknown draft id; a failed call stores nothing.
    """
    if not isinstance(draft_id, str) or not draft_id.strip():
        raise ValueError("draft_id is required")
    store = _comment_store(comment_store)
    if not store.has_draft(draft_id):
        raise LookupError("unknown draft id: {}".format(draft_id))
    comment = store.add(draft_id, entry_id, author, body, parent_id, now)
    summary = summarize_thread(store.thread(draft_id))
    return {"draft_id": draft_id, "comment": comment, "summary": summary}


def list_draft_comments(
    draft_id: str,
    comment_store: Optional[CommentStore] = None,
) -> Dict[str, Any]:
    """Return the comment thread and summary of a stored draft.

    The response is ``{"draft_id", "comments", "summary"}``. Raises
    :class:`ValueError` for a missing ``draft_id`` and :class:`LookupError` for
    an unknown draft id (when a durable draft registry is available).
    """
    if not isinstance(draft_id, str) or not draft_id.strip():
        raise ValueError("draft_id is required")
    store = _comment_store(comment_store)
    if not store.has_draft(draft_id):
        raise LookupError("unknown draft id: {}".format(draft_id))
    thread = store.thread(draft_id)
    return {
        "draft_id": draft_id,
        "comments": thread["comments"],
        "summary": summarize_thread(thread),
    }


def resolve_draft_comment(
    draft_id: str,
    comment_id: Any,
    resolved: Any,
    comment_store: Optional[CommentStore] = None,
    now: Any = None,
) -> Dict[str, Any]:
    """Set the ``resolved`` flag of a stored comment and return the thread.

    The response is ``{"draft_id", "comment", "comments", "summary"}``. The
    operation is idempotent in state. Raises :class:`ValueError` for missing or
    invalid fields, :class:`LookupError` for an unknown draft id or comment id.
    """
    if not isinstance(draft_id, str) or not draft_id.strip():
        raise ValueError("draft_id is required")
    store = _comment_store(comment_store)
    if not store.has_draft(draft_id):
        raise LookupError("unknown draft id: {}".format(draft_id))
    comment = store.resolve(draft_id, comment_id, resolved, now)
    thread = store.thread(draft_id)
    return {
        "draft_id": draft_id,
        "comment": comment,
        "comments": thread["comments"],
        "summary": summarize_thread(thread),
    }


def get_shared_draft(
    draft_id: str,
    store: Optional[Any] = None,
    review_store: Optional[ReviewStore] = None,
    comment_store: Optional[CommentStore] = None,
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
    language = _language_report(entries, workspace, template)

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

    score = compute_score(qa, entries, workspace, language)
    payload = {
        "draft_id": draft_id,
        "title": record.get("title", ""),
        "markdown": record.get("markdown", ""),
        "qa": qa.to_dict(),
        "checklist": checklist(entries, qa),
        "rules": rule_summary(entries, qa, workspace, template),
        "template": template_summary(template),
        "language": language,
        "score": score,
        "gate": score["gate"],
        "review": review,
        "created_at": record.get("created_at", ""),
    }
    if comment_store is not None:
        payload["comments"] = summarize_thread(comment_store.thread(draft_id))
    return payload


def _stored_entries(record: Dict[str, Any]) -> List[ChangeEntry]:
    """Rebuild the stored entries of a draft record."""
    return [
        _entry_from_dict(item)
        for item in (record.get("entries") or [])
        if isinstance(item, dict)
    ]


def _stored_workspace(record: Dict[str, Any]) -> WorkspaceConfig:
    """Return the workspace config that produced a stored draft."""
    config_data = record.get("config")
    if isinstance(config_data, dict):
        return WorkspaceConfig.from_dict(config_data)
    return WorkspaceConfig.default()


def _stored_template(record: Dict[str, Any]) -> ReleaseTemplate:
    """Return the template that produced a stored draft (or the default)."""
    try:
        return template_from_dict(record.get("template"))
    except ValueError:
        return default_template()


def _archive_entry(
    record: Dict[str, Any],
    review_store: Optional[ReviewStore],
    archive_store: Optional[ArchiveStore],
) -> Dict[str, Any]:
    """Build one archive listing entry from a stored draft record.

    The stored entries are re-analysed with the current heuristics so the
    ``band`` stays consistent with the rest of the product, and the current
    review status and tags are merged in. ``entry_titles`` is included for the
    free-text filter and stripped before the entry is returned to a caller.
    """
    draft_id = record.get("id")
    entries = _stored_entries(record)
    workspace = _stored_workspace(record)
    template = _stored_template(record)
    qa = analyze_entries(entries, workspace, template)
    language = _language_report(entries, workspace, template)
    score = compute_score(qa, entries, workspace, language)
    review = review_store.get(draft_id) if review_store is not None else None
    status = review.status if review is not None else REVIEW_PENDING
    tags = archive_store.tags_for(draft_id) if archive_store is not None else []
    entry_count = record.get("entry_count")
    if entry_count is None:
        entry_count = len(entries)
    return {
        "draft_id": draft_id,
        "title": record.get("title", ""),
        "created_at": record.get("created_at", ""),
        "status": status,
        "band": score["band"],
        "score": score["score"],
        "entry_count": entry_count,
        "tags": tags,
        "entry_titles": [entry.title for entry in entries if entry.title],
    }


def build_archive(
    query: Optional[str] = None,
    tag: Optional[str] = None,
    status: Optional[str] = None,
    band: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: Any = None,
    offset: Any = None,
    draft_store: Optional[DraftStore] = None,
    review_store: Optional[ReviewStore] = None,
    archive_store: Optional[ArchiveStore] = None,
) -> Dict[str, Any]:
    """Return the filtered, paginated draft archive listing.

    Every stored draft is enriched with its current review ``status``, score
    ``band``, ``entry_count`` and ``tags`` and then passed through
    :func:`releasenote.archive.filter_drafts`, which applies ``query``, ``tag``,
    ``status``, ``band`` and the ``since``/``until`` window and paginates with
    ``limit``/``offset``.

    Raises :class:`HistoryRequiredError` when ``DATA_DIR`` is unset (no
    ``draft_store``), so the server can map it to HTTP 404, and
    :class:`ValueError` for an invalid tag or filter value.
    """
    if draft_store is None:
        raise HistoryRequiredError(
            "draft archive requires history; set DATA_DIR to enable it"
        )
    records = draft_store.list_all()
    entries = [
        _archive_entry(record, review_store, archive_store)
        for record in records
        if isinstance(record, dict)
    ]
    report = filter_drafts(
        entries,
        query=query,
        tag=tag,
        status=status,
        band=band,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    report["durable"] = True
    return report


def get_archive_entry(
    draft_id: str,
    draft_store: Optional[DraftStore] = None,
    review_store: Optional[ReviewStore] = None,
    archive_store: Optional[ArchiveStore] = None,
) -> Dict[str, Any]:
    """Return one archive entry: metadata plus its tags and tag audit trail.

    Raises :class:`HistoryRequiredError` when ``DATA_DIR`` is unset and
    :class:`LookupError` for an unknown draft id.
    """
    if draft_store is None:
        raise HistoryRequiredError(
            "draft archive requires history; set DATA_DIR to enable it"
        )
    record = draft_store.get(draft_id)
    if record is None:
        raise LookupError("unknown draft id: {}".format(draft_id))
    entry = _archive_entry(record, review_store, archive_store)
    entry.pop("entry_titles", None)
    entry["audit"] = (
        archive_store.audit(draft_id) if archive_store is not None else []
    )
    return entry


def tag_draft(
    draft_id: str,
    tag: str,
    action: str,
    draft_store: Optional[DraftStore] = None,
    review_store: Optional[ReviewStore] = None,
    archive_store: Optional[ArchiveStore] = None,
) -> Dict[str, Any]:
    """Add or remove a tag on a stored draft and return its archive entry.

    ``action`` must be ``add`` or ``remove`` and ``tag`` must be valid. The
    transition is appended to the append-only ``archive.jsonl`` log. Raises
    :class:`HistoryRequiredError` when ``DATA_DIR`` is unset,
    :class:`LookupError` for an unknown draft id and :class:`ValueError` for an
    invalid tag or action; a failed call appends nothing.
    """
    if draft_store is None:
        raise HistoryRequiredError(
            "draft archive requires history; set DATA_DIR to enable it"
        )
    if not isinstance(draft_id, str) or not draft_id.strip():
        raise ValueError("draft_id is required")
    record = draft_store.get(draft_id)
    if record is None:
        raise LookupError("unknown draft id: {}".format(draft_id))
    action_norm = (action or "").strip().lower() if isinstance(action, str) else ""
    if action_norm not in ("add", "remove"):
        raise ValueError("unknown tag action: {!r}".format(action))
    validate_tag(tag)
    store = archive_store if archive_store is not None else ArchiveStore()
    store.tag(draft_id, tag, action_norm)
    return get_archive_entry(draft_id, draft_store, review_store, store)


def sample_archive() -> Dict[str, Any]:
    """Return a small, valid archive listing for the UI and tests.

    Two drafts are described so the UI can render the table and both exports
    without a round-trip to a real store.
    """
    return {
        "durable": True,
        "total": 2,
        "limit": 50,
        "offset": 0,
        "drafts": [
            {
                "draft_id": "sample-0002",
                "title": "2.0 launch",
                "created_at": "2026-01-02T10:00:00+00:00",
                "status": "approved",
                "band": "ready",
                "score": 96,
                "entry_count": 3,
                "tags": ["launch", "v2"],
            },
            {
                "draft_id": "sample-0001",
                "title": "Security patch",
                "created_at": "2026-01-01T09:00:00+00:00",
                "status": "pending",
                "band": "review",
                "score": 72,
                "entry_count": 2,
                "tags": ["security"],
            },
        ],
    }
