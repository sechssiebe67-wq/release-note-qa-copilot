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

from .models import REVIEW_PENDING, ReviewRecord


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
    ) -> Dict[str, Any]:
        record = {
            "id": uuid.uuid4().hex,
            "title": title,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "entry_count": entry_count,
            "high_flags": high_flags,
            "markdown": markdown,
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
