"""Named, reusable QA rule profiles.

A workspace can tailor the QA heuristics with a :class:`WorkspaceConfig`
(see :mod:`releasenote.config`), but an inline config cannot be named,
listed or reused. A **rule profile** bundles a human-readable ``name`` and
``description`` with a validated config so a team can attach a known rule set
to a draft, list the built-ins and see exactly which rules were effective.

The module also builds the deterministic ``rules`` summary that accompanies
every draft payload: the effective vague terms, banned phrases, required
sections, severity overrides and the number of flags that fired per code.

Only the Python standard library is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .config import WorkspaceConfig
from .models import ChangeEntry, QAResult, worst_severity
from .qa import effective_vague_terms
from .templates import ReleaseTemplate, template_summary


@dataclass
class RuleProfile:
    """A named, reusable QA rule set.

    Attributes
    ----------
    name:
        Short, unique identifier used to attach the profile to a draft.
    description:
        Human-readable explanation of what the profile changes.
    config:
        The validated :class:`~releasenote.config.WorkspaceConfig` to apply.
    """

    name: str
    description: str
    config: WorkspaceConfig

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable representation of the profile."""
        return {
            "name": self.name,
            "description": self.description,
            "config": self.config.to_dict(),
        }


class UnknownProfileError(ValueError):
    """Raised when a requested profile name is not a built-in profile."""


def _profile(name: str, description: str, config: Dict[str, Any]) -> RuleProfile:
    return RuleProfile(
        name=name,
        description=description,
        config=WorkspaceConfig.from_dict(config),
    )


# Built-in sample profiles. Each config is a valid WorkspaceConfig.
BUILTIN_PROFILES: Dict[str, RuleProfile] = {
    "strict": _profile(
        "strict",
        "Treats vague wording as high severity and requires a Breaking "
        "Changes section.",
        {
            "severity_overrides": {"vague_language": "high"},
            "required_sections": ["breaking"],
        },
    ),
    "marketing-safe": _profile(
        "marketing-safe",
        "Bans common marketing hype phrases from release notes.",
        {
            "banned_phrases": [
                "world's best",
                "best-in-class",
                "game-changing",
                "guaranteed",
                "revolutionary",
                "cutting-edge",
            ],
        },
    ),
    "internal": _profile(
        "internal",
        "Allows internal shorthand terms that would otherwise read as vague.",
        {
            "allow_terms": [
                "misc",
                "stuff",
                "things",
                "various",
                "several",
            ],
        },
    ),
}


def normalize_profile_name(value: Any) -> Optional[str]:
    """Return a trimmed profile name, or ``None`` for an absent/empty one.

    Raises :class:`ValueError` when a non-string value is supplied.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("'profile' must be a string")
    name = value.strip()
    return name or None


def get_profile(name: str) -> RuleProfile:
    """Return the built-in profile called ``name``.

    Raises :class:`UnknownProfileError` (a :class:`ValueError`) for an unknown
    or empty name.
    """
    if not isinstance(name, str) or not name.strip():
        raise UnknownProfileError("profile name is required")
    key = name.strip()
    profile = BUILTIN_PROFILES.get(key)
    if profile is None:
        raise UnknownProfileError("unknown profile: {!r}".format(key))
    return profile


def list_profiles() -> List[Dict[str, Any]]:
    """Return the built-in profiles as JSON-serialisable summaries."""
    return [BUILTIN_PROFILES[name].to_dict() for name in sorted(BUILTIN_PROFILES)]


def _dedup(values: List[str]) -> List[str]:
    """Return ``values`` with blank and case-insensitive duplicates removed."""
    seen = set()
    result: List[str] = []
    for value in values:
        token = value.strip()
        key = token.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(token)
    return result


def _merge(base: WorkspaceConfig, inline: WorkspaceConfig) -> WorkspaceConfig:
    """Layer ``inline`` config on top of ``base`` (profile) config.

    List fields are unioned; ``severity_overrides`` merge with the inline
    value winning per flag code.
    """
    overrides = dict(base.severity_overrides)
    overrides.update(inline.severity_overrides)
    return WorkspaceConfig(
        extra_vague_terms=_dedup(
            list(base.extra_vague_terms) + list(inline.extra_vague_terms)
        ),
        allow_terms=_dedup(list(base.allow_terms) + list(inline.allow_terms)),
        required_sections=_dedup(
            list(base.required_sections) + list(inline.required_sections)
        ),
        banned_phrases=_dedup(
            list(base.banned_phrases) + list(inline.banned_phrases)
        ),
        severity_overrides=overrides,
    )


def resolve_config(payload: Any) -> WorkspaceConfig:
    """Resolve a request payload into a validated :class:`WorkspaceConfig`.

    ``payload`` is the request body mapping. It may carry an inline ``config``
    object, a ``profile`` name, or both. The profile (if any) is applied first
    and the inline config is layered on top. ``None`` and an empty payload
    resolve to the default config.

    Raises :class:`UnknownProfileError` for an unknown profile and
    :class:`ValueError` for an invalid config or non-object payload.
    """
    if payload is None:
        return WorkspaceConfig.default()
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")

    name = normalize_profile_name(payload.get("profile"))
    base = WorkspaceConfig.default()
    if name is not None:
        base = get_profile(name).config

    config_payload = payload.get("config")
    if config_payload is None:
        # Clone so callers cannot mutate the shared built-in profile config.
        return WorkspaceConfig.from_dict(base.to_dict())
    inline = WorkspaceConfig.from_dict(config_payload)
    return _merge(base, inline)


def rule_summary(
    entries: List[ChangeEntry],
    qa: QAResult,
    config: Optional[WorkspaceConfig] = None,
    template: Optional[ReleaseTemplate] = None,
) -> Dict[str, Any]:
    """Return a deterministic, JSON-serialisable summary of the effective rules.

    Reports the effective vague-term list, banned phrases, required sections,
    every severity override, the resolved ``template`` and, per flag code, how
    many flags fired and the severity they resolved to.
    """
    config = config or WorkspaceConfig.default()
    flags = list(qa.flags)
    codes = sorted({flag.code for flag in flags})
    counts: Dict[str, int] = {}
    severities: Dict[str, str] = {}
    for code in codes:
        matching = [flag for flag in flags if flag.code == code]
        counts[code] = len(matching)
        severities[code] = worst_severity(matching)
    return {
        "entry_count": len(entries),
        "effective_vague_terms": effective_vague_terms(config),
        "banned_phrases": list(config.banned_phrases),
        "required_sections": list(config.required_sections),
        "severity_overrides": {
            code: config.severity_overrides[code]
            for code in sorted(config.severity_overrides)
        },
        "flag_counts": counts,
        "flag_severities": severities,
        "total_flags": len(flags),
        "template": template_summary(template),
    }


__all__ = [
    "RuleProfile",
    "UnknownProfileError",
    "BUILTIN_PROFILES",
    "normalize_profile_name",
    "get_profile",
    "list_profiles",
    "resolve_config",
    "rule_summary",
]
