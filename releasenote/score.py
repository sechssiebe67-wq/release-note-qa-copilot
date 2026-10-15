"""Deterministic release readiness score and gate policy evaluation.

A reviewer already gets individual QA flags, a checklist and a binary
``publishable`` verdict, but no graded signal for *how close a draft is to
shippable* and no way to enforce a team's own threshold. This module turns a
:class:`~releasenote.models.QAResult` into a single explainable score plus an
optional deterministic ``pass`` / ``fail`` gate decision.

The score is pure and deterministic: the same ``qa`` + ``entries`` + ``config``
always yields the same ``score``, ``band`` and ordered ``penalties``.

Only the Python standard library is used.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config import WorkspaceConfig
from .language import LANGUAGE_FLAG_CODES
from .models import (
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    QAResult,
)

# The score runs from 0 (nothing shippable) to 100 (no flags at all).
SCORE_MAX = 100
SCORE_MIN = 0

# Qualitative bands, defined by named constants so callers never hard-code
# thresholds.
BAND_READY = "ready"
BAND_REVIEW = "review"
BAND_RISKY = "risky"

BAND_READY_MIN = 85
BAND_REVIEW_MIN = 60

# Fixed penalty per flag, by severity. ``high`` > ``medium`` > ``low`` so a
# draft with many high-severity flags scores below one with only medium flags.
SEVERITY_PENALTIES = {
    SEVERITY_HIGH: 30,
    SEVERITY_MEDIUM: 15,
    SEVERITY_LOW: 5,
}

# The gate decision values.
GATE_PASS = "pass"
GATE_FAIL = "fail"

# Language lint contributes one bounded, explainable penalty rather than a
# per-flag deduction (which would double-count it, since the language findings
# are already merged into the QA flags). Each finding costs
# :data:`LANGUAGE_PENALTY_PER_FINDING` points, capped at
# :data:`LANGUAGE_PENALTY_MAX`, and is reported as a single ``language``
# ``penalties`` row.
LANGUAGE_PENALTY_CODE = "language"
LANGUAGE_PENALTY_PER_FINDING = 2
LANGUAGE_PENALTY_MAX = 20

_FALLBACK_PENALTY = SEVERITY_PENALTIES[SEVERITY_LOW]

_SEVERITY_RANK = {SEVERITY_HIGH: 3, SEVERITY_MEDIUM: 2, SEVERITY_LOW: 1}


def _worst_severity(flags: List[Any]) -> str:
    """Return the highest severity among ``flags`` (or ``medium``)."""
    if not flags:
        return SEVERITY_MEDIUM
    severities = [_severity(flag) for flag in flags]
    return max(severities, key=lambda s: _SEVERITY_RANK.get(s, 0))


def band_for(score: int) -> str:
    """Return the band name for an integer ``score``.

    ``ready`` at or above :data:`BAND_READY_MIN`, ``review`` at or above
    :data:`BAND_REVIEW_MIN`, otherwise ``risky``.
    """
    if score >= BAND_READY_MIN:
        return BAND_READY
    if score >= BAND_REVIEW_MIN:
        return BAND_REVIEW
    return BAND_RISKY


def _flags(qa: Any) -> List[Any]:
    """Return the flags carried by ``qa`` (a QAResult or a mapping)."""
    if isinstance(qa, QAResult):
        return list(qa.flags)
    if isinstance(qa, dict):
        flags = qa.get("flags")
        return list(flags) if isinstance(flags, list) else []
    flags = getattr(qa, "flags", None)
    return list(flags) if flags else []


def _entry_count(entries: Any) -> int:
    """Return the number of entries, treating any empty value as ``0``."""
    try:
        return len(entries)
    except TypeError:
        return 0


def evaluate_gate(
    score: int,
    qa: Any,
    config: Optional[WorkspaceConfig] = None,
) -> Dict[str, Any]:
    """Return the ``{status, reasons}`` gate decision for a scored draft.

    The effective policy comes from ``config``: ``min_score`` fails a draft
    whose score is below it and ``max_high`` fails a draft whose high-severity
    flag count exceeds it. With no policy configured the gate always passes
    with an empty reason list.
    """
    config = config or WorkspaceConfig.default()
    reasons: List[str] = []

    if config.min_score is not None and score < config.min_score:
        reasons.append(
            "score {} is below min_score {}".format(score, config.min_score)
        )

    high_count = len([f for f in _flags(qa) if _severity(f) == SEVERITY_HIGH])
    if config.max_high is not None and high_count > config.max_high:
        reasons.append(
            "high-severity flag count {} exceeds max_high {}".format(
                high_count, config.max_high
            )
        )

    return {
        "status": GATE_FAIL if reasons else GATE_PASS,
        "reasons": reasons,
    }


def _severity(flag: Any) -> str:
    """Return a flag's severity from a Flag object or a mapping."""
    if isinstance(flag, dict):
        return str(flag.get("severity") or "")
    return str(getattr(flag, "severity", "") or "")


def _code(flag: Any) -> str:
    """Return a flag's code from a Flag object or a mapping."""
    if isinstance(flag, dict):
        return str(flag.get("code") or "")
    return str(getattr(flag, "code", "") or "")


def compute_score(
    qa: Any,
    entries: Any,
    config: Optional[WorkspaceConfig] = None,
    language: Any = None,
) -> Dict[str, Any]:
    """Return the deterministic ``{score, band, penalties, gate}`` for a draft.

    The score starts at :data:`SCORE_MAX` and subtracts, per flag code, the
    number of flags times the severity penalty of :data:`SEVERITY_PENALTIES`,
    divided by the number of entries (so a one-entry draft is not punished like
    a fifty-entry one). The deduction is clamped to ``0..100`` and rounded to an
    integer.

    Language findings are folded in as **one bounded penalty** instead of the
    per-flag deduction: each language finding costs
    :data:`LANGUAGE_PENALTY_PER_FINDING` points, capped at
    :data:`LANGUAGE_PENALTY_MAX`, and is reported as a single ``language`` row.
    ``language`` may be the mapping returned by
    :func:`releasenote.language.compute_language`; when omitted, the language
    findings already present in ``qa`` are counted instead.

    ``penalties`` is one ``{code, severity, count, points}`` row per firing flag
    code (plus the bounded ``language`` row), sorted by ``points`` descending
    and then ``code``. ``gate`` is the ``{status, reasons}`` decision for the
    effective gate policy in ``config`` (see :func:`evaluate_gate`).
    """
    config = config or WorkspaceConfig.default()
    flags = _flags(qa)
    divisor = max(_entry_count(entries), 1)

    language_flags = [
        flag for flag in flags if _code(flag) in LANGUAGE_FLAG_CODES
    ]
    regular_flags = [
        flag for flag in flags if _code(flag) not in LANGUAGE_FLAG_CODES
    ]

    buckets: Dict[tuple, int] = {}
    for flag in regular_flags:
        key = (_code(flag), _severity(flag))
        buckets[key] = buckets.get(key, 0) + 1

    penalties: List[Dict[str, Any]] = []
    for (code, severity), count in buckets.items():
        weight = SEVERITY_PENALTIES.get(severity, _FALLBACK_PENALTY)
        points = round((weight * count) / divisor, 2)
        penalties.append(
            {
                "code": code,
                "severity": severity,
                "count": count,
                "points": points,
            }
        )

    language_count = len(language_flags)
    if isinstance(language, dict):
        findings = language.get("findings")
        if isinstance(findings, list):
            language_count = len(findings)

    if language_count:
        points = float(
            min(
                LANGUAGE_PENALTY_PER_FINDING * language_count,
                LANGUAGE_PENALTY_MAX,
            )
        )
        severity = _worst_severity(language_flags)
        penalties.append(
            {
                "code": LANGUAGE_PENALTY_CODE,
                "severity": severity,
                "count": language_count,
                "points": points,
            }
        )

    penalties.sort(key=lambda item: (-item["points"], item["code"]))

    deducted = sum(item["points"] for item in penalties)
    score = int(round(SCORE_MAX - deducted))
    score = max(SCORE_MIN, min(SCORE_MAX, score))

    return {
        "score": score,
        "band": band_for(score),
        "penalties": penalties,
        "gate": evaluate_gate(score, qa, config),
    }


__all__ = [
    "SCORE_MIN",
    "SCORE_MAX",
    "BAND_READY",
    "BAND_REVIEW",
    "BAND_RISKY",
    "BAND_READY_MIN",
    "BAND_REVIEW_MIN",
    "SEVERITY_PENALTIES",
    "GATE_PASS",
    "GATE_FAIL",
    "LANGUAGE_PENALTY_CODE",
    "LANGUAGE_PENALTY_PER_FINDING",
    "LANGUAGE_PENALTY_MAX",
    "band_for",
    "evaluate_gate",
    "compute_score",
]
