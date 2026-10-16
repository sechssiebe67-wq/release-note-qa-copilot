"""Release-note changelog digest.

A release manager rolling up several releases already has one QA-checked draft
per release, but no single machine-readable "what shipped" digest. This module
turns a list of stored draft payloads (the same shape returned by
:func:`releasenote.service.build_draft`, each with an optional ``label`` and
``date``) into one publish-ready changelog digest:

* per release: ``label``, ``date``, ``title``, ``band``, ``publishable``,
  ``counts`` (high/medium/low/total), ``top_codes`` (up to three most frequent
  flag codes), ``entry_count`` and a one-line ``summary``;
* overall: ``total_releases``, ``ready_count``, ``review_count``,
  ``risky_count`` and a deterministic ``overall_verdict`` derived from the worst
  band present.

Ordering follows the input order and every mapping is JSON-serialisable with
stable keys. The helpers are pure and dependency-free: only the Python standard
library is used.
"""

from __future__ import annotations

from typing import Any, Dict, List

BAND_READY = "ready"
BAND_REVIEW = "review"
BAND_RISKY = "risky"
BANDS = (BAND_READY, BAND_REVIEW, BAND_RISKY)

VERDICT_READY = "ready"
VERDICT_REVIEW = "review"
VERDICT_RISKY = "risky"

MIN_RELEASES = 2
MAX_RELEASES = 50

# Only the most frequent flag codes are reported, capped to keep the digest
# compact and stable.
MAX_TOP_CODES = 3

_SEVERITIES = ("high", "medium", "low")


def _as_mapping(value: Any) -> Dict[str, Any]:
    """Return ``value`` when it is a mapping, else an empty mapping."""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    """Return ``value`` when it is a list, else an empty list."""
    return list(value) if isinstance(value, list) else []


def _as_count(value: Any) -> int:
    """Return ``value`` coerced to a non-negative-ish integer count."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _flag_code(flag: Any) -> str:
    """Return a flag's code from a mapping or an object."""
    if isinstance(flag, dict):
        return str(flag.get("code") or "")
    return str(getattr(flag, "code", "") or "")


def _flag_severity(flag: Any) -> str:
    """Return a flag's severity from a mapping or an object."""
    if isinstance(flag, dict):
        return str(flag.get("severity") or "")
    return str(getattr(flag, "severity", "") or "")


def _top_codes(flags: List[Any]) -> List[str]:
    """Return up to :data:`MAX_TOP_CODES` flag codes, most frequent first.

    Ties are broken by code ascending so the result is deterministic.
    """
    counts: Dict[str, int] = {}
    for flag in flags:
        code = _flag_code(flag)
        if not code:
            continue
        counts[code] = counts.get(code, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [code for code, _count in ordered[:MAX_TOP_CODES]]


def _counts(draft: Dict[str, Any], flags: List[Any]) -> Dict[str, int]:
    """Return the high/medium/low/total counts for a release.

    The ``qa.counts`` mapping is preferred; when it is absent the counts are
    derived from the flag severities so hand-built payloads still work.
    """
    qa = _as_mapping(draft.get("qa"))
    counts = qa.get("counts")
    if isinstance(counts, dict):
        return {
            "high": _as_count(counts.get("high")),
            "medium": _as_count(counts.get("medium")),
            "low": _as_count(counts.get("low")),
            "total": _as_count(counts.get("total")),
        }
    derived = {severity: 0 for severity in _SEVERITIES}
    for flag in flags:
        severity = _flag_severity(flag)
        if severity in derived:
            derived[severity] += 1
    return {
        "high": derived["high"],
        "medium": derived["medium"],
        "low": derived["low"],
        "total": len(flags),
    }


def _band(draft: Dict[str, Any]) -> str:
    """Return the release band (``ready`` / ``review`` / ``risky``).

    The band is read from the draft's release readiness score; a payload
    without a usable score falls back to ``review``.
    """
    score = _as_mapping(draft.get("score"))
    band = score.get("band")
    if band in BANDS:
        return band
    return BAND_REVIEW


def _publishable(draft: Dict[str, Any], counts: Dict[str, int]) -> bool:
    """Return the publishable flag, defaulting to ``no high flags``."""
    qa = _as_mapping(draft.get("qa"))
    publishable = qa.get("publishable")
    if isinstance(publishable, bool):
        return publishable
    return counts["high"] == 0


def _entry_count(draft: Dict[str, Any]) -> int:
    """Return the number of entries in a release payload."""
    value = draft.get("entry_count")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    entries = draft.get("entries")
    return len(entries) if isinstance(entries, list) else 0


def _date(draft: Dict[str, Any]) -> str:
    """Return the release date string (empty when unset)."""
    value = draft.get("date")
    return value.strip() if isinstance(value, str) else ""


def _summary(
    entry_count: int, counts: Dict[str, int], publishable: bool, band: str
) -> str:
    """Return the one-line human summary for a release row."""
    noun = "entry" if entry_count == 1 else "entries"
    if counts["total"] == 0:
        flags = "no flags"
    else:
        flags = "{} flags ({} high, {} medium, {} low)".format(
            counts["total"], counts["high"], counts["medium"], counts["low"]
        )
    state = "publishable" if publishable else "not publishable"
    return "{} {}, {}; {} ({})".format(
        entry_count, noun, flags, state, band
    )


def _overall_verdict(
    ready: int, review: int, risky: int, total: int
) -> str:
    """Return the worst band present as the overall verdict."""
    if risky:
        return VERDICT_RISKY
    if ready == total:
        return VERDICT_READY
    return VERDICT_REVIEW


def _normalize(drafts: Any) -> List[Dict[str, Any]]:
    """Validate the release list, raising :class:`ValueError` on bad input."""
    if not isinstance(drafts, list):
        raise ValueError("'releases' must be a list of draft payloads")
    if len(drafts) < MIN_RELEASES or len(drafts) > MAX_RELEASES:
        raise ValueError(
            "'releases' must contain between {} and {} releases".format(
                MIN_RELEASES, MAX_RELEASES
            )
        )
    for index, item in enumerate(drafts):
        position = index + 1
        if not isinstance(item, dict):
            raise ValueError("release {} must be an object".format(position))
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(
                "release {} requires a non-empty 'label'".format(position)
            )
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(
                "release {} requires a non-empty 'title'".format(position)
            )
    return drafts


def compute_changelog(drafts: Any) -> Dict[str, Any]:
    """Build a changelog digest from a list of draft payloads.

    ``drafts`` is a list of 2–50 draft payloads (the shape returned by
    :func:`releasenote.service.build_draft`), each optionally carrying a
    ``label`` and ``date``. The releases are reported in input order and the
    result is a JSON-serialisable mapping::

        {
          "releases": [
            {"label": ..., "date": ..., "title": ..., "band": ...,
             "publishable": ..., "counts": {...}, "top_codes": [...],
             "entry_count": ..., "summary": ...}
          ],
          "total_releases": ...,
          "ready_count": ...,
          "review_count": ...,
          "risky_count": ...,
          "overall_verdict": "ready"|"review"|"risky"
        }

    Raises :class:`ValueError` for a non-list input, a release count outside
    2–50, a non-object item or an item missing ``label``/``title``.
    """
    normalized = _normalize(drafts)

    releases: List[Dict[str, Any]] = []
    ready = review = risky = 0
    for draft in normalized:
        qa = _as_mapping(draft.get("qa"))
        flags = _as_list(qa.get("flags"))
        counts = _counts(draft, flags)
        band = _band(draft)
        publishable = _publishable(draft, counts)
        entry_count = _entry_count(draft)
        releases.append(
            {
                "label": draft["label"].strip(),
                "date": _date(draft),
                "title": draft["title"].strip(),
                "band": band,
                "publishable": publishable,
                "counts": counts,
                "top_codes": _top_codes(flags),
                "entry_count": entry_count,
                "summary": _summary(entry_count, counts, publishable, band),
            }
        )
        if band == BAND_READY:
            ready += 1
        elif band == BAND_RISKY:
            risky += 1
        else:
            review += 1

    total = len(releases)
    return {
        "releases": releases,
        "total_releases": total,
        "ready_count": ready,
        "review_count": review,
        "risky_count": risky,
        "overall_verdict": _overall_verdict(ready, review, risky, total),
    }


__all__ = [
    "BAND_READY",
    "BAND_REVIEW",
    "BAND_RISKY",
    "BANDS",
    "VERDICT_READY",
    "VERDICT_REVIEW",
    "VERDICT_RISKY",
    "MIN_RELEASES",
    "MAX_RELEASES",
    "MAX_TOP_CODES",
    "compute_changelog",
]
