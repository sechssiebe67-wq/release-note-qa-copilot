"""Import changelog / ticket exports from CSV or JSON into ChangeEntry objects."""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Dict, List, Optional

from .models import (
    TYPE_BREAKING,
    TYPE_FEATURE,
    TYPE_FIX,
    TYPE_OTHER,
    ChangeEntry,
)


class ParseError(ValueError):
    """Raised when an import cannot be parsed into entries."""


# Accepted header / key aliases mapped to the canonical field name.
_FIELD_ALIASES = {
    "id": "id",
    "ticket": "id",
    "ticket_id": "id",
    "key": "id",
    "issue": "id",
    "number": "id",
    "type": "type",
    "category": "type",
    "kind": "type",
    "change_type": "type",
    "title": "title",
    "summary": "title",
    "name": "title",
    "headline": "title",
    "description": "description",
    "body": "description",
    "details": "description",
    "text": "description",
    "benefit": "benefit",
    "customer_benefit": "benefit",
    "value": "benefit",
    "impact": "benefit",
    "source": "source",
    "source_ref": "source",
    "reference": "source",
    "evidence": "source",
    "link": "link",
    "url": "link",
    "audience": "audience",
    "persona": "audience",
    "segment": "audience",
}

_TYPE_ALIASES = {
    "feature": TYPE_FEATURE,
    "features": TYPE_FEATURE,
    "new": TYPE_FEATURE,
    "added": TYPE_FEATURE,
    "add": TYPE_FEATURE,
    "enhancement": TYPE_FEATURE,
    "improvement": TYPE_FEATURE,
    "fix": TYPE_FIX,
    "fixes": TYPE_FIX,
    "bug": TYPE_FIX,
    "bugfix": TYPE_FIX,
    "bug_fix": TYPE_FIX,
    "fixed": TYPE_FIX,
    "patch": TYPE_FIX,
    "breaking": TYPE_BREAKING,
    "breaking_change": TYPE_BREAKING,
    "breaking-change": TYPE_BREAKING,
    "deprecation": TYPE_BREAKING,
    "deprecated": TYPE_BREAKING,
    "removed": TYPE_BREAKING,
    "other": TYPE_OTHER,
    "chore": TYPE_OTHER,
    "docs": TYPE_OTHER,
    "documentation": TYPE_OTHER,
    "internal": TYPE_OTHER,
}


def normalize_type(value: Any) -> str:
    """Map a free-form change type to one of the canonical types."""
    if value is None:
        return TYPE_OTHER
    token = str(value).strip().lower()
    token = re.sub(r"[\s/_-]+", "_", token)
    return _TYPE_ALIASES.get(token, TYPE_OTHER)


def _canonical_keys(record: Dict[str, Any]) -> Dict[str, Any]:
    """Rename record keys using the alias table (first match wins)."""
    result: Dict[str, Any] = {}
    for raw_key, value in record.items():
        if raw_key is None:
            continue
        key = str(raw_key).strip().lower().replace(" ", "_").replace("-", "_")
        canonical = _FIELD_ALIASES.get(key)
        if canonical and canonical not in result:
            result[canonical] = value
    return result


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(_clean(v) for v in value)
    return str(value).strip()


def _entry_from_record(record: Dict[str, Any], index: int) -> ChangeEntry:
    data = _canonical_keys(record)
    entry = ChangeEntry(
        id=_clean(data.get("id")) or "entry-{}".format(index + 1),
        type=normalize_type(data.get("type")),
        title=_clean(data.get("title")),
        description=_clean(data.get("description")),
        benefit=_clean(data.get("benefit")),
        source=_clean(data.get("source")),
        link=_clean(data.get("link")),
        audience=_clean(data.get("audience")),
        raw=dict(record),
    )
    return entry


def parse_csv(content: str) -> List[ChangeEntry]:
    """Parse a CSV export with a header row."""
    if not content or not content.strip():
        raise ParseError("CSV content is empty")
    text = content.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ParseError("CSV is missing a header row")
    canonical_headers = {
        _FIELD_ALIASES.get(
            str(h).strip().lower().replace(" ", "_").replace("-", "_")
        )
        for h in reader.fieldnames
    }
    if not canonical_headers & {"id", "title", "description", "benefit", "type"}:
        raise ParseError(
            "CSV header must contain at least one of: "
            "id, title, description, benefit, type"
        )
    entries: List[ChangeEntry] = []
    for index, row in enumerate(reader):
        if not any(_clean(v) for v in row.values()):
            continue
        entries.append(_entry_from_record(row, index))
    if not entries:
        raise ParseError("CSV contained no data rows")
    return entries


def _extract_list(payload: Any) -> List[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("entries", "changes", "items", "data", "releases"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        # A single entry object.
        if any(
            k in payload
            for k in ("title", "summary", "description", "type", "benefit")
        ):
            return [payload]
    raise ParseError("JSON must be a list of entries or contain an 'entries' list")


def parse_json(content: str) -> List[ChangeEntry]:
    """Parse a JSON export (list or object with an entries list)."""
    if not content or not content.strip():
        raise ParseError("JSON content is empty")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ParseError("Invalid JSON: {}".format(exc))
    records = _extract_list(payload)
    entries: List[ChangeEntry] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ParseError("Each JSON entry must be an object")
        entries.append(_entry_from_record(record, index))
    if not entries:
        raise ParseError("JSON contained no entries")
    return entries


def parse_entries(content: str, fmt: str = "auto") -> List[ChangeEntry]:
    """Parse entries using an explicit format or auto detection."""
    fmt = (fmt or "auto").strip().lower()
    if fmt in ("csv", "json"):
        return parse_csv(content) if fmt == "csv" else parse_json(content)
    if fmt != "auto":
        raise ParseError("Unsupported format: {!r}".format(fmt))
    stripped = content.lstrip("\ufeff").lstrip()
    if stripped.startswith(("[", "{")):
        return parse_json(content)
    return parse_csv(content)


def sample_csv() -> str:
    """Return a small example CSV payload used by the UI and tests."""
    return (
        "id,type,title,description,benefit,source,link,audience\n"
        "REL-101,feature,Bulk export,Export all reports as CSV in one click,"
        "Saves ~30 minutes per weekly report,REL-101,"
        "https://tracker.example.com/REL-101,admins\n"
        "REL-102,fix,Timezone fix,Timestamps now render in the user timezone,"
        "No more confusing dates,REL-102,,everyone\n"
        "REL-103,breaking,API v1 removal,The legacy /v1 endpoints are removed,"
        "Cleaner and faster,REL-103,,developers\n"
        "REL-104,feature,Better dashboard,The dashboard is improved,"
        "A much better experience,REL-104,,everyone\n"
    )


def sample_json() -> str:
    """Return a small example JSON payload."""
    return json.dumps(
        {
            "entries": [
                {
                    "id": "REL-201",
                    "type": "feature",
                    "title": "SSO login",
                    "description": "SAML single sign-on for workspaces",
                    "benefit": "Central access control for IT",
                    "source": "REL-201",
                    "link": "https://tracker.example.com/REL-201",
                    "audience": "security teams",
                },
                {
                    "id": "REL-202",
                    "type": "breaking",
                    "title": "Config format change",
                    "description": "config.yml replaces config.ini",
                    "benefit": "Consistent configuration",
                    "source": "REL-202",
                    "audience": "developers",
                },
            ]
        },
        indent=2,
    )
