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
