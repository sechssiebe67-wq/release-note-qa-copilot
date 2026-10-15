"""Workspace-level QA rule configuration.

A workspace can tailor the built-in QA heuristics to its own vocabulary and
tone: extra vague terms, allowed (non-vague) terms, banned marketing phrases,
required sections and per-flag severity overrides. The configuration is loaded
from a JSON file or an inline API payload and validated strictly so a typo
never silently disables a review rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .models import SEVERITY_HIGH, SEVERITY_LOW, SEVERITY_MEDIUM

VALID_SEVERITIES = (SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW)

# Keys understood by :meth:`WorkspaceConfig.from_dict`. Anything else is a
# mistake and must be rejected so rules are never silently ignored.
KNOWN_KEYS = (
    "extra_vague_terms",
    "allow_terms",
    "required_sections",
    "banned_phrases",
    "severity_overrides",
)

_LIST_KEYS = (
    "extra_vague_terms",
    "allow_terms",
    "required_sections",
    "banned_phrases",
)


@dataclass
class WorkspaceConfig:
    """Team-specific overrides applied on top of the built-in QA rules.

    Attributes
    ----------
    extra_vague_terms:
        Terms appended to the built-in vague-word list.
    allow_terms:
        Terms removed from the effective vague list (case-insensitive).
    required_sections:
        Canonical group keys that must contain at least one entry.
    severity_overrides:
        Maps a flag code to ``high``/``medium``/``low``.
    banned_phrases:
        Literal phrases that must never appear in a release note.
    """

    extra_vague_terms: List[str] = field(default_factory=list)
    allow_terms: List[str] = field(default_factory=list)
    required_sections: List[str] = field(default_factory=list)
    severity_overrides: Dict[str, str] = field(default_factory=dict)
    banned_phrases: List[str] = field(default_factory=list)

    @classmethod
    def default(cls) -> "WorkspaceConfig":
        """Return the empty configuration (built-in rules only)."""
        return cls()

    @classmethod
    def from_dict(cls, payload: Any) -> "WorkspaceConfig":
        """Build a validated config from a JSON-like mapping.

        ``None`` yields the default config. Unknown keys, non-string list
        values and non-canonical severities raise :class:`ValueError`.
        """
        if payload is None:
            return cls.default()
        if not isinstance(payload, dict):
            raise ValueError("config must be a JSON object")
        unknown = [key for key in payload if key not in KNOWN_KEYS]
        if unknown:
            raise ValueError(
                "unknown config key(s): {}".format(", ".join(sorted(unknown)))
            )

        lists: Dict[str, List[str]] = {}
        for key in _LIST_KEYS:
            value = payload.get(key, [])
            if not isinstance(value, list):
                raise ValueError("config '{}' must be a list of strings".format(key))
            if not all(isinstance(item, str) for item in value):
                raise ValueError(
                    "config '{}' must be a list of strings".format(key)
                )
            lists[key] = [item.strip() for item in value if item.strip()]

        overrides = payload.get("severity_overrides", {})
        if not isinstance(overrides, dict):
            raise ValueError("config 'severity_overrides' must be an object")
        cleaned: Dict[str, str] = {}
        for code, severity in overrides.items():
            if not isinstance(code, str) or not code.strip():
                raise ValueError(
                    "config 'severity_overrides' keys must be non-empty strings"
                )
            if severity not in VALID_SEVERITIES:
                raise ValueError(
                    "severity for '{}' must be one of: {}".format(
                        code, ", ".join(VALID_SEVERITIES)
                    )
                )
            cleaned[code.strip()] = severity

        return cls(
            extra_vague_terms=lists["extra_vague_terms"],
            allow_terms=lists["allow_terms"],
            required_sections=lists["required_sections"],
            banned_phrases=lists["banned_phrases"],
            severity_overrides=cleaned,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable representation of the config."""
        return {
            "extra_vague_terms": list(self.extra_vague_terms),
            "allow_terms": list(self.allow_terms),
            "required_sections": list(self.required_sections),
            "banned_phrases": list(self.banned_phrases),
            "severity_overrides": dict(self.severity_overrides),
        }

    def is_default(self) -> bool:
        """True when no rule is customised."""
        return not (
            self.extra_vague_terms
            or self.allow_terms
            or self.required_sections
            or self.banned_phrases
            or self.severity_overrides
        )


def sample_config() -> Dict[str, Any]:
    """A documented example config served by ``GET /api/config/sample``."""
    return {
        "extra_vague_terms": ["blazing", "revolutionary"],
        "allow_terms": ["seamless"],
        "required_sections": ["feature", "breaking"],
        "banned_phrases": ["world's best", "guaranteed"],
        "severity_overrides": {"vague_language": "high"},
    }
