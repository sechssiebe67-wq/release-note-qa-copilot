"""Data model for release-note entries and QA results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

TYPE_FEATURE = "feature"
TYPE_FIX = "fix"
TYPE_BREAKING = "breaking"
TYPE_OTHER = "other"

# Order used when grouping and rendering a draft.
GROUP_ORDER = [TYPE_FEATURE, TYPE_FIX, TYPE_BREAKING, TYPE_OTHER]

GROUP_TITLES = {
    TYPE_FEATURE: "Features",
    TYPE_FIX: "Fixes",
    TYPE_BREAKING: "Breaking Changes",
    TYPE_OTHER: "Other Changes",
}

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

_SEVERITY_RANK = {SEVERITY_HIGH: 3, SEVERITY_MEDIUM: 2, SEVERITY_LOW: 1}


@dataclass
class ChangeEntry:
    """A single changelog or ticket derived change."""

    id: str = ""
    type: str = TYPE_OTHER
    title: str = ""
    description: str = ""
    benefit: str = ""
    source: str = ""
    link: str = ""
    audience: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def reference(self) -> str:
        """Best available human readable source reference."""
        return self.source or self.link or ""


@dataclass
class Flag:
    """A single QA finding on an entry or on the whole draft."""

    code: str
    severity: str
    message: str
    entry_id: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "entry_id": self.entry_id,
        }


@dataclass
class QAResult:
    """Aggregated QA outcome for a set of entries."""

    flags: List[Flag] = field(default_factory=list)

    def flags_for(self, entry_id: str) -> List[Flag]:
        return [f for f in self.flags if f.entry_id == entry_id]

    @property
    def high(self) -> List[Flag]:
        return [f for f in self.flags if f.severity == SEVERITY_HIGH]

    @property
    def medium(self) -> List[Flag]:
        return [f for f in self.flags if f.severity == SEVERITY_MEDIUM]

    @property
    def low(self) -> List[Flag]:
        return [f for f in self.flags if f.severity == SEVERITY_LOW]

    @property
    def publishable(self) -> bool:
        """No high severity finding may remain before publishing."""
        return not self.high

    def to_dict(self) -> Dict[str, Any]:
        return {
            "publishable": self.publishable,
            "counts": {
                "high": len(self.high),
                "medium": len(self.medium),
                "low": len(self.low),
                "total": len(self.flags),
            },
            "flags": [f.to_dict() for f in self.flags],
        }


def worst_severity(flags: List[Flag]) -> str:
    """Return the highest severity found in ``flags`` (or ``low``)."""
    if not flags:
        return SEVERITY_LOW
    return max(flags, key=lambda f: _SEVERITY_RANK.get(f.severity, 0)).severity
