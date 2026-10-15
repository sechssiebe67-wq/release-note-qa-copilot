"""QA heuristics: flag vague, unsupported or incomplete release-note claims."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .config import WorkspaceConfig
from .grouping import group_entries
from .models import (
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    TYPE_BREAKING,
    TYPE_FEATURE,
    ChangeEntry,
    Flag,
    QAResult,
)


# Words that make a benefit statement hard to verify.
VAGUE_TERMS = (
    "improved",
    "improvement",
    "better",
    "best",
    "various",
    "several",
    "optimized",
    "optimised",
    "enhanced",
    "robust",
    "seamless",
    "simplified",
    "misc",
    "stuff",
    "things",
    "some",
    "many",
    "faster",
    "quicker",
    "easier",
    "user-friendly",
    "state-of-the-art",
    "world-class",
)

_VAGUE_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in VAGUE_TERMS) + r")\b",
    re.IGNORECASE,
)


def _effective_vague_terms(config: WorkspaceConfig) -> List[str]:
    """Combine built-in terms with extra terms minus allowed terms."""
    allowed = {t.strip().lower() for t in config.allow_terms if t.strip()}
    terms: List[str] = []
    seen = set()
    for term in list(VAGUE_TERMS) + list(config.extra_vague_terms):
        token = term.strip()
        key = token.lower()
        if not key or key in allowed or key in seen:
            continue
        seen.add(key)
        terms.append(token)
    return terms


def _vague_pattern(config: WorkspaceConfig):
    """Return a compiled vague-term regex for the effective term list."""
    if config.is_default():
        return _VAGUE_RE
    terms = _effective_vague_terms(config)
    if not terms:
        return None
    return re.compile(
        r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b",
        re.IGNORECASE,
    )


def _with_override(flag: Flag, config: WorkspaceConfig) -> Flag:
    """Apply a configured severity override to ``flag`` (in place)."""
    override = config.severity_overrides.get(flag.code)
    if override in (SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW):
        flag.severity = override
    return flag


# Quantified statements such as "50%", "3x", "2 seconds", "5 days".
_QUANT_RE = re.compile(
    r"(?:\b\d+(?:[.,]\d+)?\s?%"
    r"|\b\d+(?:[.,]\d+)?\s?x\b"
    r"|\b\d+(?:[.,]\d+)?\s?(?:ms|s|sec|secs|seconds?|min|mins|minutes?|"
    r"h|hr|hrs|hours?|days?|weeks?|kb|mb|gb|tb)\b"
    r"|\b\d+(?:[.,]\d+)?\s?(?:times|fold)\b)",
    re.IGNORECASE,
)

# Signals that a breaking change documents required user action.
_MIGRATION_RE = re.compile(
    r"\b(migrat\w*|action required|must |need to |deprecat\w*|removed|"
    r"replac\w*|upgrade guide|breaking)\b",
    re.IGNORECASE,
)


def _entry_text(entry: ChangeEntry) -> str:
    return " ".join(
        part for part in (entry.title, entry.description, entry.benefit) if part
    )


def analyze_entry(
    entry: ChangeEntry, config: Optional[WorkspaceConfig] = None
) -> List[Flag]:
    """Return QA flags for a single entry.

    When ``config`` is omitted the built-in rules are used unchanged.
    """
    config = config or WorkspaceConfig.default()
    flags: List[Flag] = []

    def add(code: str, severity: str, message: str) -> None:
        flags.append(
            _with_override(
                Flag(
                    code=code,
                    severity=severity,
                    message=message,
                    entry_id=entry.id,
                ),
                config,
            )
        )

    text = _entry_text(entry)
    has_source = bool(entry.source or entry.link)

    if not entry.title.strip():
        add("missing_title", SEVERITY_HIGH, "Entry has no title.")

    if not entry.description.strip():
        add(
            "missing_description",
            SEVERITY_MEDIUM,
            "Entry has no description, so the change is hard to verify.",
        )

    if entry.type == TYPE_FEATURE and not entry.benefit.strip():
        add(
            "missing_benefit",
            SEVERITY_MEDIUM,
            "Feature has no customer benefit statement.",
        )

    if not has_source:
        add(
            "missing_source",
            SEVERITY_HIGH,
            "No source or link, so no core statement can be traced.",
        )

    pattern = _vague_pattern(config)
    vague = (
        sorted({m.group(0).lower() for m in pattern.finditer(text)})
        if pattern is not None
        else []
    )
    if vague:
        add(
            "vague_language",
            SEVERITY_MEDIUM,
            "Vague wording without measurable meaning: {}.".format(
                ", ".join(vague)
            ),
        )

    lowered = text.lower()
    for phrase in config.banned_phrases:
        if phrase and phrase.lower() in lowered:
            add(
                "banned_phrase",
                SEVERITY_MEDIUM,
                "Banned phrase used: '{}'.".format(phrase),
            )

    if _QUANT_RE.search(text) and not has_source:
        add(
            "quantified_claim_without_source",
            SEVERITY_HIGH,
            "Quantified claim has no source to back the number.",
        )

    if entry.type == TYPE_BREAKING and not _MIGRATION_RE.search(text):
        add(
            "breaking_without_migration",
            SEVERITY_MEDIUM,
            "Breaking change does not describe the required migration or action.",
        )

    return flags


def analyze_entries(
    entries: List[ChangeEntry], config: Optional[WorkspaceConfig] = None
) -> QAResult:
    """Return QA flags for a whole set of entries.

    Applies the per-entry rules plus draft-level checks (configured required
    sections). With no ``config`` the built-in rules are unchanged.
    """
    config = config or WorkspaceConfig.default()
    result = QAResult()
    for entry in entries:
        result.flags.extend(analyze_entry(entry, config))

    if config.required_sections:
        groups = group_entries(entries)
        for section in config.required_sections:
            if not groups.get(section):
                result.flags.append(
                    _with_override(
                        Flag(
                            code="missing_required_section",
                            severity=SEVERITY_MEDIUM,
                            message=(
                                "Required section '{}' has no entries.".format(
                                    section
                                )
                            ),
                            entry_id="",
                        ),
                        config,
                    )
                )
    return result


def checklist(entries: List[ChangeEntry], qa: QAResult) -> List[Dict[str, str]]:
    """Build a short, human readable QA checklist for the draft."""
    total = len(entries)
    flagged_ids = {f.entry_id for f in qa.flags}
    unsourced = [
        e.id for e in entries if not (e.source or e.link)
    ]
    vague_ids = {f.entry_id for f in qa.flags if f.code == "vague_language"}
    quantified_unsourced = {
        f.entry_id
        for f in qa.flags
        if f.code == "quantified_claim_without_source"
    }
    breaking = [e for e in entries if e.type == TYPE_BREAKING]
    breaking_flagged = {
        f.entry_id
        for f in qa.flags
        if f.code == "breaking_without_migration"
    }

    def status(ok: bool, warn: bool = False) -> str:
        if ok:
            return "pass"
        return "warn" if warn else "fail"

    items = [
        {
            "label": "Every entry has a source or link",
            "status": status(not unsourced),
            "detail": (
                "All {} entries traceable".format(total)
                if not unsourced
                else "Untraceable: {}".format(", ".join(unsourced))
            ),
        },
        {
            "label": "No unsupported quantified claims",
            "status": status(not quantified_unsourced),
            "detail": (
                "No numbers without a source"
                if not quantified_unsourced
                else "Back these with a source: {}".format(
                    ", ".join(sorted(quantified_unsourced))
                )
            ),
        },
        {
            "label": "No vague, unmeasurable wording",
            "status": status(not vague_ids, warn=True),
            "detail": (
                "No vague terms found"
                if not vague_ids
                else "Rewrite entries: {}".format(", ".join(sorted(vague_ids)))
            ),
        },
        {
            "label": "Breaking changes describe the migration",
            "status": status(
                all(e.id not in breaking_flagged for e in breaking)
            ),
            "detail": (
                "{} breaking change(s) documented".format(len(breaking))
                if breaking
                else "No breaking changes"
            ),
        },
        {
            "label": "Draft is publishable (no high severity flags)",
            "status": status(qa.publishable),
            "detail": (
                "Ready for review"
                if qa.publishable
                else "{} high severity flag(s) remain".format(len(qa.high))
            ),
        },
    ]
    # A passing checklist is only meaningful if entries were reviewed.
    if total and not flagged_ids:
        items[-1]["detail"] = "Ready for review · no flags"
    if total == 0:
        for item in items:
            item["status"] = "fail"
            item["detail"] = "No entries imported"
    return items
