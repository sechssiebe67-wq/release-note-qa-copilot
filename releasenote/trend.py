"""Multi-release trend analysis.

A single draft or a pairwise diff answers "is *this* release fine?" but not
"is our release quality getting better or worse over the last N releases?".
This module answers the second question deterministically and offline: given
an ordered list of 2–20 release payloads it parses and QA-checks each one with
the same rules as a normal draft, then compares consecutive releases.

The result is a pure, JSON-serialisable mapping::

    {
      "releases": [
        {"label": ..., "entry_count": ..., "counts": {...},
         "publishable": ..., "sourced_ratio": ...,
         "vague_hits": ..., "banned_hits": ...}
      ],
      "deltas": [
        {"from": ..., "to": ..., "high": ..., "medium": ..., "total": ...,
         "sourced_ratio": ..., "verdict": "improving"|"stable"|"regressing"}
      ],
      "overall": {"verdict": ..., "best": ..., "worst": ...}
    }

Only the Python standard library is used.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from .config import WorkspaceConfig
from .importer import parse_entries
from .models import ChangeEntry
from .profiles import resolve_config
from .qa import analyze_entries

MIN_RELEASES = 2
MAX_RELEASES = 20

VERDICT_IMPROVING = "improving"
VERDICT_STABLE = "stable"
VERDICT_REGRESSING = "regressing"

ConfigInput = Optional[Union[WorkspaceConfig, Dict[str, Any]]]


def _coerce_workspace(
    config: ConfigInput, profile: Optional[str]
) -> WorkspaceConfig:
    """Resolve ``config``/``profile`` into a validated workspace config.

    A :class:`WorkspaceConfig` is used as-is; otherwise the mapping (or
    ``None``) together with an optional profile name is resolved by
    :func:`releasenote.profiles.resolve_config`.
    """
    if isinstance(config, WorkspaceConfig):
        return config
    payload: Dict[str, Any] = {}
    if config is not None:
        payload["config"] = config
    if profile is not None:
        payload["profile"] = profile
    return resolve_config(payload)


def _normalize_releases(releases: Any) -> List[Dict[str, str]]:
    """Validate and normalise the ordered release list.

    Raises :class:`ValueError` for a non-list, a count outside
    ``MIN_RELEASES``..``MAX_RELEASES``, a non-object item, or an item with
    empty ``content``. A missing/blank label becomes ``Release N``.
    """
    if not isinstance(releases, list):
        raise ValueError("'releases' must be a list of 2–20 release objects")
    if len(releases) < MIN_RELEASES or len(releases) > MAX_RELEASES:
        raise ValueError(
            "'releases' must contain between {} and {} releases".format(
                MIN_RELEASES, MAX_RELEASES
            )
        )
    normalized: List[Dict[str, str]] = []
    for index, item in enumerate(releases):
        position = index + 1
        if not isinstance(item, dict):
            raise ValueError("release {} must be an object".format(position))
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(
                "release {} requires non-empty 'content'".format(position)
            )
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            label = "Release {}".format(position)
        fmt = item.get("format", "auto")
        if not isinstance(fmt, str):
            raise ValueError(
                "release {} 'format' must be a string".format(position)
            )
        normalized.append(
            {"label": label.strip(), "content": content, "format": fmt}
        )
    return normalized


def _sourced_ratio(entries: List[ChangeEntry]) -> float:
    """Fraction of entries carrying a source or link (0.0 for no entries)."""
    if not entries:
        return 0.0
    sourced = sum(1 for entry in entries if entry.source or entry.link)
    return sourced / len(entries)


def _counts(qa: Any) -> Dict[str, int]:
    """Return the high/medium/low/total flag counts for a QA result."""
    return {
        "high": len(qa.high),
        "medium": len(qa.medium),
        "low": len(qa.low),
        "total": len(qa.flags),
    }


def _compare(previous: Dict[str, Any], current: Dict[str, Any]) -> str:
    """Classify ``current`` against ``previous`` deterministically.

    Compares the ``high`` count first, then ``total``, then
    ``sourced_ratio``. Fewer high flags, fewer total flags and a higher
    sourced ratio are all improvements. ``improving`` is returned when the
    first differing metric got strictly better, ``regressing`` when strictly
    worse, and ``stable`` when every compared metric is equal.
    """
    previous_counts = previous["counts"]
    current_counts = current["counts"]
    for field, lower_is_better in (
        ("high", True),
        ("total", True),
    ):
        before = previous_counts[field]
        after = current_counts[field]
        if before == after:
            continue
        better = after < before if lower_is_better else after > before
        return VERDICT_IMPROVING if better else VERDICT_REGRESSING

    before = previous["sourced_ratio"]
    after = current["sourced_ratio"]
    if before == after:
        return VERDICT_STABLE
    return VERDICT_IMPROVING if after > before else VERDICT_REGRESSING


def _delta(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """Return the signed delta and verdict between two releases."""
    return {
        "from": previous["label"],
        "to": current["label"],
        "high": current["counts"]["high"] - previous["counts"]["high"],
        "medium": current["counts"]["medium"] - previous["counts"]["medium"],
        "total": current["counts"]["total"] - previous["counts"]["total"],
        "sourced_ratio": current["sourced_ratio"] - previous["sourced_ratio"],
        "verdict": _compare(previous, current),
    }


def _quality_key(release: Dict[str, Any]) -> Any:
    """Sort key where smaller means better quality (fewer/lower flags)."""
    return (
        release["counts"]["high"],
        release["counts"]["total"],
        -release["sourced_ratio"],
    )


def _overall(releases: List[Dict[str, Any]]) -> Dict[str, str]:
    """Return the overall verdict plus the best and worst release labels."""
    verdict = _compare(releases[0], releases[-1])
    best = min(releases, key=_quality_key)
    worst = max(releases, key=_quality_key)
    return {
        "verdict": verdict,
        "best": best["label"],
        "worst": worst["label"],
    }


def compute_trend(
    releases: Any,
    config: ConfigInput = None,
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyse an ordered list of releases and return a trend report.

    ``releases`` is a list of 2–20 ``{label, content, format}`` mappings.
    Each content string is parsed with :func:`releasenote.importer.parse_entries`
    and QA-checked with :func:`releasenote.qa.analyze_entries` under the
    resolved ``config``/``profile``.

    Raises :class:`ValueError` for an invalid release list/config and
    :class:`releasenote.importer.ParseError` for unparseable content.
    """
    workspace = _coerce_workspace(config, profile)
    normalized = _normalize_releases(releases)

    per_release: List[Dict[str, Any]] = []
    for item in normalized:
        entries = parse_entries(item["content"], item["format"])
        qa = analyze_entries(entries, workspace)
        per_release.append(
            {
                "label": item["label"],
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
            }
        )

    deltas = [
        _delta(previous, current)
        for previous, current in zip(per_release, per_release[1:])
    ]

    return {
        "releases": per_release,
        "deltas": deltas,
        "overall": _overall(per_release),
    }


__all__ = [
    "MIN_RELEASES",
    "MAX_RELEASES",
    "VERDICT_IMPROVING",
    "VERDICT_STABLE",
    "VERDICT_REGRESSING",
    "compute_trend",
]
