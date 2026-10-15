"""Side-by-side comparison of QA rule profiles.

A reviewer can attach a named rule profile (see :mod:`releasenote.profiles`)
to a draft, but there is no way to see how the **same** changelog would score
under every available profile at once. Without that, choosing a profile is
guesswork: a clean draft may be clean because the entries are good or because
the profile was lenient.

This module answers the question directly, deterministically and offline:

* parse the ``content`` **once** with :func:`releasenote.importer.parse_entries`,
* for each requested profile resolve the effective config with
  :func:`releasenote.profiles.resolve_config` (layering an optional inline
  ``config`` on top) and run :func:`releasenote.qa.analyze_entries`,
* return per-profile metrics, the delta against a baseline profile and a single
  deterministic recommendation.

It reuses the existing importer, QA engine, config resolution and metric
helpers, so it adds no new heuristics and no third-party dependency.

The result is a pure, JSON-serialisable mapping::

    {
      "baseline": "strict",
      "profiles": [
        {"name": ..., "description": ..., "entry_count": ...,
         "counts": {"high": ..., "medium": ..., "low": ..., "total": ...},
         "publishable": ..., "sourced_ratio": ...,
         "vague_hits": ..., "banned_hits": ...,
         "flag_counts": {...}, "flag_severities": {...},
         "delta": {"high": ..., "total": ..., "sourced_ratio": ...}}
      ],
      "recommendation": {"profile": ..., "reason": ...}
    }

Only the Python standard library is used.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

from .config import WorkspaceConfig
from .importer import parse_entries
from .models import ChangeEntry, QAResult, worst_severity
from .profiles import (
    BUILTIN_PROFILES,
    RuleProfile,
    get_profile,
    resolve_config,
)
from .qa import analyze_entries
from .trend import _counts, _quality_key, _sourced_ratio

ConfigInput = Optional[Union[WorkspaceConfig, Dict[str, Any]]]


def _normalize_profiles(profiles: Any) -> List[str]:
    """Validate the requested profile list into a deduplicated name list.

    ``None`` selects every built-in profile in its stable definition order.
    A non-list value, an empty list or a non-string/blank entry raises
    :class:`ValueError`; an unknown name raises
    :class:`releasenote.profiles.UnknownProfileError`.
    """
    if profiles is None:
        return list(BUILTIN_PROFILES)
    if not isinstance(profiles, list):
        raise ValueError("'profiles' must be a list of profile names")
    if not profiles:
        raise ValueError("'profiles' must contain at least one profile name")
    names: List[str] = []
    seen = set()
    for item in profiles:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("each profile name must be a non-empty string")
        name = item.strip()
        if name in seen:
            continue
        seen.add(name)
        get_profile(name)  # Raises UnknownProfileError for an unknown name.
        names.append(name)
    return names


def _inline_config(config: ConfigInput) -> Optional[Dict[str, Any]]:
    """Return the inline config as a mapping, or ``None`` when unset."""
    if config is None:
        return None
    if isinstance(config, WorkspaceConfig):
        return config.to_dict()
    if not isinstance(config, dict):
        raise ValueError("'config' must be a JSON object")
    return config


def _flag_summary(qa: QAResult) -> Tuple[Dict[str, int], Dict[str, str]]:
    """Return per-code flag counts and the worst severity of each code."""
    codes = sorted({flag.code for flag in qa.flags})
    counts: Dict[str, int] = {}
    severities: Dict[str, str] = {}
    for code in codes:
        matching = [flag for flag in qa.flags if flag.code == code]
        counts[code] = len(matching)
        severities[code] = worst_severity(matching)
    return counts, severities


def _metric_row(
    name: str,
    description: str,
    entries: List[ChangeEntry],
    qa: QAResult,
) -> Dict[str, Any]:
    """Build one per-profile metrics row, mirroring the trend/compare shape."""
    flag_counts, flag_severities = _flag_summary(qa)
    return {
        "name": name,
        "description": description,
        "entry_count": len(entries),
        "counts": _counts(qa),
        "publishable": qa.publishable,
        "sourced_ratio": _sourced_ratio(entries),
        "vague_hits": sum(
            1 for flag in qa.flags if flag.code == "vague_language"
        ),
        "banned_hits": sum(
            1 for flag in qa.flags if flag.code == "banned_phrase"
        ),
        "flag_counts": flag_counts,
        "flag_severities": flag_severities,
    }


def _recommend(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    """Pick the best profile using the deterministic rule.

    Fewer ``high`` flags, then fewer ``total`` flags, then a higher
    ``sourced_ratio``; ties fall back to input order (``min`` is stable). The
    ``reason`` names the winning metrics, mirroring
    :func:`releasenote.compare._recommend`.
    """
    best = min(rows, key=_quality_key)
    reason = (
        "Fewest high-severity flags ({high}), fewest total flags ({total}) "
        "and highest sourced ratio ({ratio:.2f})."
    ).format(
        high=best["counts"]["high"],
        total=best["counts"]["total"],
        ratio=best["sourced_ratio"],
    )
    return {"profile": best["name"], "reason": reason}


def compare_profiles(
    content: str,
    format: str = "auto",
    profiles: Any = None,
    config: ConfigInput = None,
) -> Dict[str, Any]:
    """QA-check one changelog under several rule profiles side by side.

    ``content`` is parsed once with
    :func:`releasenote.importer.parse_entries`. Each profile in ``profiles``
    (default: every built-in, in definition order) is resolved with an optional
    inline ``config`` layered on top and analysed with
    :func:`releasenote.qa.analyze_entries`.

    The first profile in the list is the ``baseline``; every row carries a
    ``delta`` of its ``high``, ``total`` and ``sourced_ratio`` against it. The
    ``recommendation`` picks the profile with the fewest ``high`` flags, then
    the fewest ``total`` flags, then the highest ``sourced_ratio`` (ties fall
    back to input order).

    Raises :class:`releasenote.profiles.UnknownProfileError` for an unknown
    requested profile, :class:`ValueError` for a non-list ``profiles`` value or
    invalid config, and :class:`releasenote.importer.ParseError` for
    unparseable content.
    """
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content is required")
    fmt = format or "auto"
    if not isinstance(fmt, str):
        raise ValueError("'format' must be a string")

    names = _normalize_profiles(profiles)
    inline = _inline_config(config)

    entries = parse_entries(content, fmt)
    rows: List[Dict[str, Any]] = []
    for name in names:
        profile: RuleProfile = get_profile(name)
        payload: Dict[str, Any] = {"profile": name}
        if inline is not None:
            payload["config"] = inline
        workspace = resolve_config(payload)
        qa = analyze_entries(entries, workspace)
        rows.append(
            _metric_row(name, profile.description, entries, qa)
        )

    baseline = rows[0]
    for row in rows:
        row["delta"] = {
            "high": row["counts"]["high"] - baseline["counts"]["high"],
            "total": row["counts"]["total"] - baseline["counts"]["total"],
            "sourced_ratio": row["sourced_ratio"] - baseline["sourced_ratio"],
        }

    return {
        "baseline": baseline["name"],
        "profiles": rows,
        "recommendation": _recommend(rows),
    }


__all__ = ["compare_profiles"]
