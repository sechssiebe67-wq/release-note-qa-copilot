"""Optional file-backed store for generated draft history.

The app stays stateless by default. When ``DATA_DIR`` is configured the
server appends every generated draft to a JSON-lines file so teams can
revisit earlier release notes.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .archive import TAG_ACTIONS, TAG_ADD, validate_tag
from .comments import add_comment as _add_comment
from .comments import resolve_comment as _resolve_comment
from .models import REVIEW_PENDING, ReviewRecord
from .versioning import next_version, validate_draft_key


class DraftStore:
    """Thread-safe append-only store for generated drafts."""

    def __init__(self, data_dir: str, max_entries: int = 500) -> None:
        self.data_dir = data_dir
        self.max_entries = max_entries
        self.path = os.path.join(data_dir, "drafts.jsonl")
        os.makedirs(data_dir, exist_ok=True)
        self._lock = threading.Lock()

    def _read_all(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        records: List[Dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def save(
        self,
        title: str,
        markdown: str,
        entry_count: int,
        high_flags: int,
        entries: Optional[List[Dict[str, Any]]] = None,
        config: Optional[Dict[str, Any]] = None,
        profile: Optional[str] = None,
        template: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append a generated draft and return the stored record.

        ``entries``, ``config`` and ``template`` are the parsed entries, the
        resolved workspace config and the resolved template that produced the
        draft. They are optional so the original four-argument call keeps
        working, but storing them lets a share link re-run the QA heuristics on
        the exact source data later.
        """
        record = {
            "id": uuid.uuid4().hex,
            "title": title,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "entry_count": entry_count,
            "high_flags": high_flags,
            "markdown": markdown,
            "entries": list(entries or []),
            "config": dict(config or {}),
            "profile": profile,
            "template": dict(template or {}),
        }
        with self._lock:
            records = self._read_all()
            records.append(record)
            if len(records) > self.max_entries:
                records = records[-self.max_entries :]
            tmp_path = self.path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                for item in records:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            os.replace(tmp_path, self.path)
        return record

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            records = self._read_all()
        summaries = []
        for record in reversed(records):
            summaries.append(
                {
                    "id": record.get("id"),
                    "title": record.get("title"),
                    "created_at": record.get("created_at"),
                    "entry_count": record.get("entry_count"),
                    "high_flags": record.get("high_flags"),
                }
            )
        if limit > 0:
            summaries = summaries[:limit]
        return summaries

    def list_all(self) -> List[Dict[str, Any]]:
        """Return every stored draft record (oldest first).

        Unlike :meth:`list` this returns the full records, including the stored
        entries, so callers such as the draft archive can re-run the QA
        heuristics and match on entry titles.
        """
        with self._lock:
            return list(self._read_all())

    def get(self, draft_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            records = self._read_all()
        for record in records:
            if record.get("id") == draft_id:
                return record
        return None


class ReviewStore:
    """Append-only review state and audit log for drafts.

    When ``data_dir`` is set, review state is written to
    ``<data_dir>/reviews.jsonl`` (latest record per draft wins) and every
    transition is also appended to ``<data_dir>/audit.jsonl``. When
    ``data_dir`` is unset the store falls back to in-memory dictionaries, so
    the API keeps working but the records are lost on restart.

    ``draft_store`` is optional. When supplied, a review may only be recorded
    for a draft id that store knows about; otherwise (in-memory fallback or no
    draft registry) any draft id is accepted because existence cannot be
    validated.
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        draft_store: Optional[DraftStore] = None,
    ) -> None:
        self.data_dir = data_dir
        self.draft_store = draft_store
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)
            self.reviews_path: Optional[str] = os.path.join(
                data_dir, "reviews.jsonl"
            )
            self.audit_path: Optional[str] = os.path.join(data_dir, "audit.jsonl")
        else:
            self.reviews_path = None
            self.audit_path = None
        self._lock = threading.Lock()
        self._reviews: Dict[str, Dict[str, Any]] = {}
        self._audit: List[Dict[str, Any]] = []

    # -- persistence helpers --------------------------------------------
    def _read_jsonl(self, path: Optional[str]) -> List[Dict[str, Any]]:
        if not path or not os.path.exists(path):
            return []
        records: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def _append_jsonl(self, path: str, record: Dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_reviews(self) -> Dict[str, Dict[str, Any]]:
        """Return the latest review record per draft id."""
        if self.reviews_path:
            records = self._read_jsonl(self.reviews_path)
        else:
            records = list(self._reviews.values())
        state: Dict[str, Dict[str, Any]] = {}
        for record in records:
            draft_id = record.get("draft_id")
            if draft_id is None:
                continue
            state[draft_id] = record
        return state

    def _read_audit(self) -> List[Dict[str, Any]]:
        if self.audit_path:
            return self._read_jsonl(self.audit_path)
        return list(self._audit)

    def _next_seq(self) -> int:
        entries = self._read_audit()
        if not entries:
            return 1
        return max(int(entry.get("seq", 0)) for entry in entries) + 1

    # -- public API ------------------------------------------------------
    def has_draft(self, draft_id: str) -> bool:
        """Whether ``draft_id`` may be reviewed.

        Without a draft registry (in-memory fallback) existence cannot be
        checked, so any non-empty id is accepted.
        """
        if self.draft_store is not None:
            return self.draft_store.get(draft_id) is not None
        return True

    def record(
        self,
        draft_id: str,
        status: str,
        reviewer: str = "",
        comment: str = "",
    ) -> ReviewRecord:
        """Append a transition and return the updated :class:`ReviewRecord`."""
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock:
            previous = self._read_reviews().get(draft_id, {}).get(
                "status", REVIEW_PENDING
            )
            entry = {
                "seq": self._next_seq(),
                "draft_id": draft_id,
                "status": status,
                "reviewer": reviewer,
                "comment": comment,
                "updated_at": timestamp,
                "previous_status": previous,
            }
            if self.reviews_path and self.audit_path:
                self._append_jsonl(self.reviews_path, entry)
                self._append_jsonl(self.audit_path, entry)
            else:
                self._reviews[draft_id] = entry
                self._audit.append(entry)
        return ReviewRecord(
            id=draft_id,
            status=status,
            reviewer=reviewer,
            comment=comment,
            updated_at=timestamp,
        )

    def get(self, draft_id: str) -> Optional[ReviewRecord]:
        """Return the latest review state for ``draft_id`` (or ``None``)."""
        with self._lock:
            entry = self._read_reviews().get(draft_id)
        if entry is None:
            return None
        return ReviewRecord(
            id=draft_id,
            status=entry.get("status", REVIEW_PENDING),
            reviewer=entry.get("reviewer", ""),
            comment=entry.get("comment", ""),
            updated_at=entry.get("updated_at", ""),
        )

    def audit(self, draft_id: str) -> List[Dict[str, Any]]:
        """Return the audit transitions for ``draft_id`` in order."""
        with self._lock:
            entries = self._read_audit()
        selected = [e for e in entries if e.get("draft_id") == draft_id]
        return sorted(selected, key=lambda e: e.get("seq", 0))


class ArchiveStore:
    """Append-only tag log for stored drafts.

    Every tag transition is appended to ``<data_dir>/archive.jsonl`` as one
    JSON object per line with a monotonically increasing ``seq``; the current
    tag set is the latest line per ``(draft_id, tag)``. Because the log is
    append-only the full tag history stays recoverable.

    When ``data_dir`` is unset the store falls back to an in-memory list, so
    the object can still be constructed and exercised but nothing survives a
    restart. :attr:`is_durable` reports which mode is active.
    """

    def __init__(self, data_dir: Optional[str] = None) -> None:
        self.data_dir = data_dir
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)
            self.path: Optional[str] = os.path.join(data_dir, "archive.jsonl")
        else:
            self.path = None
        self._lock = threading.Lock()
        self._transitions: List[Dict[str, Any]] = []

    @property
    def is_durable(self) -> bool:
        """Whether transitions are persisted to disk (``DATA_DIR`` set)."""
        return self.path is not None

    # -- persistence helpers --------------------------------------------
    def _read_jsonl(self, path: Optional[str]) -> List[Dict[str, Any]]:
        if not path or not os.path.exists(path):
            return []
        records: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def _append_jsonl(self, path: str, record: Dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_all(self) -> List[Dict[str, Any]]:
        if self.path:
            return self._read_jsonl(self.path)
        return list(self._transitions)

    def _next_seq(self, records: List[Dict[str, Any]]) -> int:
        if not records:
            return 1
        return max(int(record.get("seq", 0)) for record in records) + 1

    # -- public API ------------------------------------------------------
    def tag(self, draft_id: str, tag: str, action: str) -> Dict[str, Any]:
        """Append one tag transition and return the stored record.

        ``action`` must be ``add`` or ``remove`` and ``tag`` must be a valid
        tag; :class:`ValueError` is raised otherwise and nothing is appended.
        """
        normalized_tag = validate_tag(tag)
        normalized_action = (action or "").strip().lower()
        if normalized_action not in TAG_ACTIONS:
            raise ValueError("unknown tag action: {!r}".format(action))
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock:
            records = self._read_all()
            record = {
                "seq": self._next_seq(records),
                "draft_id": draft_id,
                "tag": normalized_tag,
                "action": normalized_action,
                "updated_at": timestamp,
            }
            if self.path:
                self._append_jsonl(self.path, record)
            else:
                self._transitions.append(record)
        return record

    def current_tags(self) -> Dict[str, List[str]]:
        """Return the current tag set per draft id (latest action wins)."""
        with self._lock:
            records = self._read_all()
        latest: Dict[Any, str] = {}
        for record in sorted(records, key=lambda item: item.get("seq", 0)):
            draft_id = record.get("draft_id")
            tag = record.get("tag")
            if draft_id is None or tag is None:
                continue
            latest[(draft_id, tag)] = record.get("action")
        state: Dict[str, List[str]] = {}
        for (draft_id, tag), action in latest.items():
            if action == TAG_ADD:
                state.setdefault(draft_id, []).append(tag)
        return {draft_id: sorted(tags) for draft_id, tags in state.items()}

    def tags_for(self, draft_id: str) -> List[str]:
        """Return the current tags of ``draft_id`` (empty when untagged)."""
        return self.current_tags().get(draft_id, [])

    def audit(self, draft_id: str) -> List[Dict[str, Any]]:
        """Return the tag transitions for ``draft_id`` in ``seq`` order."""
        with self._lock:
            records = self._read_all()
        selected = [r for r in records if r.get("draft_id") == draft_id]
        return sorted(selected, key=lambda item: item.get("seq", 0))


class DraftVersionStore:
    """Append-only store of immutable draft versions.

    Every version of a logical draft (identified by a caller-supplied
    ``draft_key``) is appended to ``<data_dir>/versions.jsonl`` as one JSON
    object per line carrying a monotonically increasing ``seq``, the canonical
    ``draft_key``, the integer ``version``, the ``draft_id`` of the stored
    draft, an ISO-8601 UTC ``created_at`` and the full ``payload``. The
    **latest line per ``(draft_key, version)``** wins when reading, so an
    accidental rewrite can be superseded without losing the log.

    When ``data_dir`` is unset the store falls back to an in-memory list, so
    the object can still be constructed and exercised but nothing survives a
    restart. :attr:`is_durable` reports which mode is active.
    """

    def __init__(self, data_dir: Optional[str] = None) -> None:
        self.data_dir = data_dir
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)
            self.path: Optional[str] = os.path.join(data_dir, "versions.jsonl")
        else:
            self.path = None
        self._lock = threading.Lock()
        self._records: List[Dict[str, Any]] = []

    @property
    def is_durable(self) -> bool:
        """Whether versions are persisted to disk (``DATA_DIR`` set)."""
        return self.path is not None

    # -- persistence helpers --------------------------------------------
    def _read_jsonl(self, path: Optional[str]) -> List[Dict[str, Any]]:
        if not path or not os.path.exists(path):
            return []
        records: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def _append_jsonl(self, path: str, record: Dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_all(self) -> List[Dict[str, Any]]:
        if self.path:
            return self._read_jsonl(self.path)
        return list(self._records)

    def _next_seq(self, records: List[Dict[str, Any]]) -> int:
        if not records:
            return 1
        return max(int(record.get("seq", 0)) for record in records) + 1

    @staticmethod
    def _coerce_version(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    # -- public API ------------------------------------------------------
    def _versions_for(
        self, records: List[Dict[str, Any]], draft_key: str
    ) -> List[Dict[str, Any]]:
        """Return the latest record per version for ``draft_key``, ascending."""
        selected = [
            record
            for record in records
            if record.get("draft_key") == draft_key
        ]
        latest: Dict[int, Dict[str, Any]] = {}
        for record in sorted(selected, key=lambda item: item.get("seq", 0)):
            latest[self._coerce_version(record.get("version"))] = record
        return [latest[version] for version in sorted(latest)]

    def append(
        self,
        draft_key: str,
        draft_id: Optional[str],
        payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Append one immutable version and return the stored record.

        The next version is ``max(existing) + 1`` for this ``draft_key`` (or
        ``1`` when it has no versions yet), so callers never have to track the
        sequence themselves.
        """
        canonical = validate_draft_key(draft_key)
        with self._lock:
            records = self._read_all()
            existing = [
                record.get("version")
                for record in records
                if record.get("draft_key") == canonical
            ]
            version = next_version(existing)
            stored_payload = dict(payload or {})
            stored_payload["draft_key"] = canonical
            stored_payload["version"] = version
            record = {
                "seq": self._next_seq(records),
                "draft_key": canonical,
                "version": version,
                "draft_id": draft_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "payload": stored_payload,
            }
            if self.path:
                self._append_jsonl(self.path, record)
            else:
                self._records.append(record)
        return record

    def list_versions(self, draft_key: str) -> List[Dict[str, Any]]:
        """Return every version of ``draft_key`` in ascending order."""
        canonical = validate_draft_key(draft_key)
        with self._lock:
            records = self._read_all()
        return self._versions_for(records, canonical)

    def get_version(
        self, draft_key: str, version: Any
    ) -> Optional[Dict[str, Any]]:
        """Return the record for ``(draft_key, version)`` or ``None``."""
        canonical = validate_draft_key(draft_key)
        target = self._coerce_version(version)
        for record in self.list_versions(canonical):
            if self._coerce_version(record.get("version")) == target:
                return record
        return None

    def latest(self, draft_key: str) -> Optional[Dict[str, Any]]:
        """Return the highest-versioned record of ``draft_key`` or ``None``."""
        records = self.list_versions(draft_key)
        return records[-1] if records else None


class CommentStore:
    """Append-only per-entry discussion log for stored drafts.

    Every comment and every resolution is appended to
    ``<data_dir>/comments.jsonl`` as one JSON object per line carrying a
    monotonically increasing ``seq``, the ``draft_id``, an ``op`` (``add`` or
    ``resolve``) and the event payload. A thread is reconstructed by replaying
    the events for one draft in ``seq`` order, so the log is append-only and the
    full discussion history stays recoverable.

    When ``data_dir`` is unset the store falls back to an in-memory list, so the
    comment feature keeps working in ephemeral runs but nothing survives a
    restart. :attr:`is_durable` reports which mode is active.

    ``draft_store`` is optional. When supplied, a comment may only be added to,
    listed for or resolved against a draft id that store knows about; otherwise
    existence cannot be validated and any non-empty draft id is accepted (the
    same rule :class:`ReviewStore` uses).
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        draft_store: Optional[DraftStore] = None,
    ) -> None:
        self.data_dir = data_dir
        self.draft_store = draft_store
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)
            self.path: Optional[str] = os.path.join(data_dir, "comments.jsonl")
        else:
            self.path = None
        self._lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []

    @property
    def is_durable(self) -> bool:
        """Whether comments are persisted to disk (``DATA_DIR`` set)."""
        return self.path is not None

    # -- persistence helpers --------------------------------------------
    def _read_jsonl(self, path: Optional[str]) -> List[Dict[str, Any]]:
        if not path or not os.path.exists(path):
            return []
        records: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def _append_jsonl(self, path: str, record: Dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_all(self) -> List[Dict[str, Any]]:
        if self.path:
            return self._read_jsonl(self.path)
        return list(self._events)

    def _next_seq(self, records: List[Dict[str, Any]]) -> int:
        if not records:
            return 1
        return max(int(record.get("seq", 0)) for record in records) + 1

    def _thread(self, events: List[Dict[str, Any]], draft_id: str) -> Dict[str, Any]:
        """Reconstruct the comment thread of ``draft_id`` from ``events``."""
        selected = [
            event for event in events if event.get("draft_id") == draft_id
        ]
        comments: List[Dict[str, Any]] = []
        index: Dict[Any, Dict[str, Any]] = {}
        for event in sorted(selected, key=lambda item: item.get("seq", 0)):
            op = event.get("op")
            if op == "add":
                comment = dict(event.get("comment") or {})
                comments.append(comment)
                index[comment.get("id")] = comment
            elif op == "resolve":
                comment = index.get(event.get("comment_id"))
                if comment is not None:
                    comment["resolved"] = bool(event.get("resolved"))
                    comment["resolved_at"] = event.get("updated_at")
        return {"draft_id": draft_id, "comments": comments}

    # -- public API ------------------------------------------------------
    def has_draft(self, draft_id: str) -> bool:
        """Whether ``draft_id`` may carry comments.

        Without a draft registry (in-memory fallback) existence cannot be
        checked, so any non-empty id is accepted.
        """
        if self.draft_store is not None:
            return self.draft_store.get(draft_id) is not None
        return True

    def thread(self, draft_id: str) -> Dict[str, Any]:
        """Return the reconstructed thread of ``draft_id`` (never ``None``)."""
        with self._lock:
            events = self._read_all()
        return self._thread(events, draft_id)

    def add(
        self,
        draft_id: str,
        entry_id: Any,
        author: Any = "",
        body: Any = "",
        parent_id: Any = None,
        now: Any = None,
    ) -> Dict[str, Any]:
        """Append one comment and return the stored comment record.

        Validation is delegated to :func:`releasenote.comments.add_comment`, so
        :class:`ValueError` is raised for a bad body/entry and
        :class:`LookupError` for an unknown ``parent_id``; nothing is appended in
        either case.
        """
        if now is None:
            now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            events = self._read_all()
            thread = self._thread(events, draft_id)
            _new_thread, comment = _add_comment(
                thread, entry_id, author, body, parent_id, now
            )
            event = {
                "seq": self._next_seq(events),
                "draft_id": draft_id,
                "op": "add",
                "comment": comment,
            }
            if self.path:
                self._append_jsonl(self.path, event)
            else:
                self._events.append(event)
        return comment

    def resolve(
        self,
        draft_id: str,
        comment_id: Any,
        resolved: Any,
        now: Any = None,
    ) -> Dict[str, Any]:
        """Append a resolution event and return the updated comment.

        Idempotent in state: resolving an already-resolved comment to the same
        value leaves the thread unchanged (one audit line is still written). An
        unknown comment id raises :class:`LookupError`.
        """
        if not isinstance(comment_id, str) or not comment_id.strip():
            raise ValueError("comment_id is required")
        comment_id = comment_id.strip()
        if now is None:
            timestamp = datetime.now(timezone.utc).isoformat()
        elif isinstance(now, datetime):
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            timestamp = now.astimezone(timezone.utc).isoformat()
        elif isinstance(now, str):
            timestamp = now
        else:
            raise ValueError("'now' must be an ISO-8601 string or a datetime")
        with self._lock:
            events = self._read_all()
            thread = self._thread(events, draft_id)
            new_thread = _resolve_comment(thread, comment_id, resolved)
            event = {
                "seq": self._next_seq(events),
                "draft_id": draft_id,
                "op": "resolve",
                "comment_id": comment_id,
                "resolved": bool(resolved),
                "updated_at": timestamp,
            }
            if self.path:
                self._append_jsonl(self.path, event)
            else:
                self._events.append(event)
        return next(
            comment
            for comment in new_thread["comments"]
            if comment.get("id") == comment_id
        )

    def comments_for(self, draft_id: str) -> List[Dict[str, Any]]:
        """Return the reconstructed comments of ``draft_id`` in order."""
        return self.thread(draft_id)["comments"]

    def all_comments(self) -> List[Dict[str, Any]]:
        """Return every comment across all drafts in creation (``seq``) order.

        Each returned comment carries an extra ``draft_id`` key (the pure
        comment shape does not), which the export handlers may use to group or
        filter rows.
        """
        with self._lock:
            events = self._read_all()
        comments: List[Dict[str, Any]] = []
        index: Dict[Any, Dict[str, Any]] = {}
        for event in sorted(events, key=lambda item: item.get("seq", 0)):
            op = event.get("op")
            if op == "add":
                comment = dict(event.get("comment") or {})
                comment["draft_id"] = event.get("draft_id")
                comments.append(comment)
                index[(event.get("draft_id"), comment.get("id"))] = comment
            elif op == "resolve":
                comment = index.get(
                    (event.get("draft_id"), event.get("comment_id"))
                )
                if comment is not None:
                    comment["resolved"] = bool(event.get("resolved"))
                    comment["resolved_at"] = event.get("updated_at")
        return comments
