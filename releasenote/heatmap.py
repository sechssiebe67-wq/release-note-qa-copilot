"""Deterministic release-note risk heatmap.

A reviewer already gets individual QA flags, a release readiness score and a
binary ``publishable`` verdict, but no single view of *where* the remaining
risk sits. This module turns a draft payload (the shape returned by
:func:`releasenote.service.build_draft`) into a compact, explainable **risk
heatmap**:

* a per-section heatmap keyed by the canonical groups (``feature``, ``fix``,
  ``breaking``, ``other``) with the flag count, the per-severity counts, a
  ``weighted_risk`` and a ``heat`` band;
* a per-entry heatmap ordered by descending weighted risk (ties broken by
  entry id ascending);
* a per-code summary ordered by descending weighted risk then code ascending;
* an overall ``total_weighted_risk``, the ``hottest_section`` and a
  deterministic ``ship`` / ``hold`` recommendation with a human-readable
  ``reason``.

Weighting
---------

Every flag contributes a fixed, documented weight by severity::

    high = 3, medium = 2, low = 1

so ``weighted_risk == 3*high + 2*medium + 1*low`` for every section, entry and
code row. The band thresholds are fixed:

===============  =============
``weighted_risk``  ``heat``
===============  =============
``0``              ``cool``
``1 .. 3``         ``warm``
``>= 4``           ``hot``
===============  =============

A flag is attributed to the section of the entry named by its ``entry_id``; a
draft-level flag (empty ``entry_id``, such as a missing required section) falls
under ``other``. The decision rule is deterministic: ``hold`` when any
high-severity flag remains or the readiness band (from the payload's
``score.band``) is ``risky``; otherwise ``ship``.

The helpers are pure, deterministic and dependency-free: only the Python
standard library is used.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .config import WorkspaceConfig
from .models import (
    GROUP_ORDER,
    GROUP_TITLES,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    TYPE_OTHER,
)

# Fixed severity weighting. ``weighted_risk`` is always
# ``high*3 + medium*2 + low*1``.
SEVERITY_WEIGHTS = {
    SEVERITY_HIGH: 3,
    SEVERITY_MEDIUM: 2,
    SEVERITY_LOW: 1,
}

# Heat bands. ``cool`` means no weighted risk, ``warm`` covers 1–3 and ``hot``
# is 4 or more.
HEAT_COOL = "cool"
HEAT_WARM = "warm"
HEAT_HOT = "hot"
HOT_MIN = 4
WARM_MAX = 3

# The decision values.
SHIP = "ship"
HOLD = "hold"

# Canonical section order (matches the grouped draft payload and the UI).
SECTIONS: Tuple[str, ...] = tuple(GROUP_ORDER)


def heat_for(weighted_risk: Any) -> str:
    """Return the heat band for an integer ``weighted_risk``.

    ``cool`` at ``0``, ``warm`` for ``1..3`` and ``hot`` for ``>= 4``.
    Non-integer input is coerced with ``int``; unparseable values are treated
    as ``0``.
    """
    try:
        value = int(weighted_risk)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return HEAT_COOL
    if value <= WARM_MAX:
        return HEAT_WARM
    return HEAT_HOT


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return ``value`` when it is a mapping, else an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    """Return ``value`` when it is a list, else an empty list."""
    return list(value) if isinstance(value, list) else []


def _code(flag: Any) -> str:
    """Return a flag's code from a mapping or an object."""
    if isinstance(flag, Mapping):
        return str(flag.get("code") or "")
    return str(getattr(flag, "code", "") or "")


def _severity(flag: Any) -> str:
    """Return a flag's severity from a mapping or an object."""
    if isinstance(flag, Mapping):
        return str(flag.get("severity") or "")
    return str(getattr(flag, "severity", "") or "")


def _entry_id(flag: Any) -> str:
    """Return a flag's entry id from a mapping or an object."""
    if isinstance(flag, Mapping):
        return str(flag.get("entry_id") or "")
    return str(getattr(flag, "entry_id", "") or "")


def _coerce_config(config: Any) -> WorkspaceConfig:
    """Normalise a config mapping / object / ``None`` into a config.

    Raises :class:`ValueError` when the payload fails validation.
    """
    if config is None:
        return WorkspaceConfig.default()
    if isinstance(config, WorkspaceConfig):
        return config
    return WorkspaceConfig.from_dict(config)


FlagRecord = Tuple[str, str, str]


def _flag_records(
    flags: Sequence[Any], config: WorkspaceConfig
) -> List[FlagRecord]:
    """Return ``(code, severity, entry_id)`` tuples with overrides applied."""
    overrides = config.severity_overrides
    records: List[FlagRecord] = []
    for flag in flags:
        code = _code(flag)
        severity = overrides.get(code, _severity(flag))
        records.append((code, severity, _entry_id(flag)))
    return records


def _bucket(records: Sequence[FlagRecord]) -> Dict[str, Any]:
    """Return the severity counts, weighted risk and heat for ``records``."""
    high = sum(1 for code, severity, _eid in records if severity == SEVERITY_HIGH)
    medium = sum(
        1 for code, severity, _eid in records if severity == SEVERITY_MEDIUM
    )
    low = sum(1 for code, severity, _eid in records if severity == SEVERITY_LOW)
    flag_count = high + medium + low
    weighted = high * 3 + medium * 2 + low * 1
    return {
        "flag_count": flag_count,
        "high": high,
        "medium": medium,
        "low": low,
        "weighted_risk": weighted,
        "heat": heat_for(weighted),
    }


def _section_for(entry_type: Any) -> str:
    """Return the canonical section key for an entry's ``type``."""
    key = str(entry_type or "")
    return key if key in SECTIONS else TYPE_OTHER


def _readiness_band(payload: Mapping[str, Any]) -> str:
    """Return the draft's readiness band (``""`` when unavailable)."""
    score = _as_mapping(payload.get("score"))
    band = score.get("band")
    return band if isinstance(band, str) else ""


def _recommendation(records: Sequence[FlagRecord], band: str) -> Dict[str, Any]:
    """Return the deterministic ``{decision, reason}`` recommendation.

    ``hold`` when any high-severity flag remains or the readiness band is
    ``risky``, otherwise ``ship``. The ``reason`` names the deciding factor.
    """
    high = sum(1 for _code_v, severity, _eid in records if severity == SEVERITY_HIGH)
    if high:
        return {
            "decision": HOLD,
            "reason": "{} high-severity flag(s) remain.".format(high),
        }
    if band == "risky":
        return {
            "decision": HOLD,
            "reason": "readiness band is 'risky'.",
        }
    if band:
        return {
            "decision": SHIP,
            "reason": (
                "no high-severity flags and readiness band is '{}'.".format(band)
            ),
        }
    return {
        "decision": SHIP,
        "reason": "no high-severity flags.",
    }


def _validate(payload: Any) -> Tuple[List[Any], List[Any]]:
    """Validate the payload and return ``(entries, flags)``.

    Raises :class:`ValueError` when the payload is not an object, the
    ``entries``/``qa`` keys are missing or malformed, or when the payload
    carries an invalid workspace config.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("heatmap payload must be a JSON object")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("heatmap payload requires an 'entries' list")
    qa = payload.get("qa")
    if not isinstance(qa, Mapping):
        raise ValueError("heatmap payload requires a 'qa' object")
    flags = qa.get("flags")
    if not isinstance(flags, list):
        raise ValueError("heatmap payload requires a 'qa.flags' list")
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("every entry must be an object")
    return entries, flags


def compute_heatmap(
    payload: Any, config: Any = None
) -> Dict[str, Any]:
    """Return the deterministic risk heatmap for a draft payload.

    ``payload`` is the shape returned by
    :func:`releasenote.service.build_draft`: an ``entries`` list plus a ``qa``
    object carrying ``flags`` (and optionally a ``score`` object whose ``band``
    drives the recommendation). ``config`` is an optional
    :class:`~releasenote.config.WorkspaceConfig` (or mapping); its
    ``severity_overrides`` are applied before weighting and an invalid config
    raises :class:`ValueError`.

    The returned mapping is JSON-serialisable with a stable key order::

        {
          "sections": [{key, title, entry_count, flag_count, high, medium,
                        low, weighted_risk, heat}],
          "entries":  [{entry_id, title, type, flag_count, high, medium, low,
                        weighted_risk, heat}],
          "codes":    [{code, severity, count, flag_count, high, medium, low,
                        weighted_risk, heat}],
          "total_weighted_risk": ...,
          "hottest_section": ...,
          "recommendation": {"decision": "ship"|"hold", "reason": ...},
          "readiness_band": ...,
          "high_flag_count": ...
        }

    Running it twice on the same payload yields an identical mapping.
    """
    entries, flags = _validate(payload)
    workspace = _coerce_config(config if config is not None else payload.get("config"))
    records = _flag_records(flags, workspace)

    # Attribute every entry to a canonical section and index entries by id so
    # flags can be attached back to their entry.
    entry_section: Dict[str, str] = {}
    entry_order: List[Mapping[str, Any]] = []
    for entry in entries:
        item = _as_mapping(entry)
        entry_order.append(item)
        entry_id = str(item.get("id") or "")
        entry_section[entry_id] = _section_for(item.get("type"))

    # Section buckets. Every flag lands in exactly one section, so the section
    # weighted risks sum to the overall total.
    section_records: Dict[str, List[FlagRecord]] = {key: [] for key in SECTIONS}
    for record in records:
        section = entry_section.get(record[2], TYPE_OTHER)
        section_records[section].append(record)

    section_entries: Dict[str, int] = {key: 0 for key in SECTIONS}
    for item in entry_order:
        section_entries[_section_for(item.get("type"))] += 1

    sections: List[Dict[str, Any]] = []
    total = 0
    hottest = ""
    hottest_risk = 0
    for key in SECTIONS:
        bucket = _bucket(section_records[key])
        bucket_row = {
            "key": key,
            "title": GROUP_TITLES.get(key, key.title()),
            "entry_count": section_entries[key],
        }
        bucket_row.update(bucket)
        sections.append(bucket_row)
        total += bucket["weighted_risk"]
        if bucket["weighted_risk"] > hottest_risk:
            hottest_risk = bucket["weighted_risk"]
            hottest = key

    # Per-entry rows, ordered by descending weighted risk then entry id.
    entry_rows: List[Dict[str, Any]] = []
    for item in entry_order:
        entry_id = str(item.get("id") or "")
        matching = [
            record for record in records if record[2] == entry_id
        ]
        bucket = _bucket(matching)
        row = {
            "entry_id": entry_id,
            "title": str(item.get("title") or ""),
            "type": _section_for(item.get("type")),
        }
        row.update(bucket)
        entry_rows.append(row)
    entry_rows.sort(key=lambda row: (-row["weighted_risk"], row["entry_id"]))

    # Per-code summary, ordered by descending weighted risk then code.
    by_code: Dict[str, List[FlagRecord]] = {}
    for record in records:
        by_code.setdefault(record[0], []).append(record)
    code_rows: List[Dict[str, Any]] = []
    for code, code_records in by_code.items():
        bucket = _bucket(code_records)
        severities = [record[1] for record in code_records]
        severity = max(
            severities,
            key=lambda item: SEVERITY_WEIGHTS.get(item, 0),
        )
        row = {
            "code": code,
            "severity": severity,
            "count": bucket["flag_count"],
        }
        row.update(bucket)
        code_rows.append(row)
    code_rows.sort(key=lambda row: (-row["weighted_risk"], row["code"]))

    band = _readiness_band(payload)
    high_flag_count = sum(
        1 for _code_v, severity, _eid in records if severity == SEVERITY_HIGH
    )

    return {
        "sections": sections,
        "entries": entry_rows,
        "codes": code_rows,
        "total_weighted_risk": total,
        "hottest_section": hottest,
        "recommendation": _recommendation(records, band),
        "readiness_band": band,
        "high_flag_count": high_flag_count,
    }


__all__ = [
    "SEVERITY_WEIGHTS",
    "HEAT_COOL",
    "HEAT_WARM",
    "HEAT_HOT",
    "HOT_MIN",
    "WARM_MAX",
    "SHIP",
    "HOLD",
    "SECTIONS",
    "heat_for",
    "compute_heatmap",
]
