"""Deterministic, offline **language lint** for release-note drafts.

Traceability (``qa.py``) and hype checks (``profiles.py``) tell a reviewer
*what* is risky, but not whether the writing itself is *readable* and
*consistent in voice*. This module scores each draft on readability and style
and surfaces concrete findings (long sentences, passive voice, first-person
slips, ALL-CAPS, emoji, jargon, mixed tense and duplicate headings).

Everything here is pure and dependency free: a fixed sentence splitter, a
deterministic syllable counter, fixed pattern/term/tense-marker lists and no
randomness, network access or external packages. The output is
JSON-serialisable so it can be folded straight into the draft payload.

Only the Python standard library is used.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .config import WorkspaceConfig
from .models import Flag, SEVERITY_HIGH, SEVERITY_LOW, SEVERITY_MEDIUM

# -- flag codes ----------------------------------------------------------
LONG_SENTENCE = "long_sentence"
PASSIVE_VOICE = "passive_voice"
FIRST_PERSON = "first_person"
ALL_CAPS = "all_caps"
EMOJI = "emoji_in_release_note"
JARGON = "jargon"
TENSE_INCONSISTENCY = "tense_inconsistency"
DUPLICATE_HEADING = "duplicate_heading"

#: Every code this module can emit. ``score.py`` uses this to treat language
#: findings as one bounded penalty rather than double-counting them.
LANGUAGE_FLAG_CODES = (
    LONG_SENTENCE,
    PASSIVE_VOICE,
    FIRST_PERSON,
    ALL_CAPS,
    EMOJI,
    JARGON,
    TENSE_INCONSISTENCY,
    DUPLICATE_HEADING,
)

#: Built-in severity per code (before ``severity_overrides``).
LANGUAGE_SEVERITIES = {
    LONG_SENTENCE: SEVERITY_MEDIUM,
    PASSIVE_VOICE: SEVERITY_LOW,
    FIRST_PERSON: SEVERITY_LOW,
    ALL_CAPS: SEVERITY_LOW,
    EMOJI: SEVERITY_LOW,
    JARGON: SEVERITY_MEDIUM,
    TENSE_INCONSISTENCY: SEVERITY_LOW,
    DUPLICATE_HEADING: SEVERITY_MEDIUM,
}

#: A sentence at or above this word count is flagged ``long_sentence``.
LONG_SENTENCE_WORDS = 40

#: Up to this many findings is ``acceptable``; more is ``dense``.
ACCEPTABLE_FINDING_LIMIT = 3

GRADE_CLEAR = "clear"
GRADE_ACCEPTABLE = "acceptable"
GRADE_DENSE = "dense"

#: Fixed jargon/buzzword list. Matched as whole words (or phrases).
JARGON_TERMS = (
    "leverage",
    "leveraging",
    "synergy",
    "synergies",
    "synergize",
    "utilize",
    "utilization",
    "incentivize",
    "paradigm",
    "holistic",
    "actionable",
    "deliverable",
    "bandwidth",
    "ideate",
    "disruptive",
    "low-hanging fruit",
    "circle back",
    "move the needle",
    "at the end of the day",
    "value-add",
    "touch base",
    "boots on the ground",
)

#: Fixed tense markers. The benefit lines are scanned for these to detect a
#: draft that mixes past and present tense.
PAST_TENSE_MARKERS = (
    "fixed",
    "added",
    "removed",
    "improved",
    "updated",
    "resolved",
    "introduced",
    "enabled",
    "disabled",
    "shipped",
    "delivered",
    "reduced",
    "increased",
    "changed",
    "made",
    "built",
    "renamed",
    "migrated",
    "released",
    "deprecated",
    "refactored",
    "was",
    "were",
    "had",
)
PRESENT_TENSE_MARKERS = (
    "adds",
    "removes",
    "improves",
    "updates",
    "resolves",
    "introduces",
    "enables",
    "disables",
    "ships",
    "delivers",
    "reduces",
    "increases",
    "changes",
    "makes",
    "builds",
    "renames",
    "migrates",
    "releases",
    "deprecates",
    "refactors",
    "saves",
    "lets",
    "provides",
    "allows",
    "helps",
    "supports",
    "keeps",
    "gives",
    "shows",
    "works",
    "means",
    "ensures",
    "prevents",
    "renders",
    "uses",
    "offers",
    "brings",
)

# Passive voice: a form of "to be"/"get" followed by a past participle.
_PASSIVE_RE = re.compile(
    r"\b(?:was|were|is|are|am|be|been|being|get|gets|got)\s+"
    r"[a-z]+(?:ed|en)\b",
    re.IGNORECASE,
)

_FIRST_PERSON_RE = re.compile(
    r"\b(?:I|we|our|ours|us|my|mine|myself|ourselves|we're|we've|I'm|I've)\b",
    re.IGNORECASE,
)

_ALL_CAPS_RE = re.compile(r"\b[A-Z]{4,}\b")

# Common uppercase acronyms that are not "shouting".
_CAPS_ALLOW = frozenset(
    {
        "JSON",
        "HTML",
        "HTTP",
        "HTTPS",
        "YAML",
        "REST",
        "XML",
        "UUID",
        "SAML",
        "LDAP",
        "OIDC",
        "GRPC",
        "POST",
        "HEAD",
        "PATCH",
        "DELETE",
    }
)

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u26FF\u2700-\u27BF\u2B00-\u2BFF\uFE0F]"
)

_JARGON_RE = re.compile(
    r"\b(" + "|".join(re.escape(term) for term in JARGON_TERMS) + r")\b",
    re.IGNORECASE,
)
_PAST_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in PAST_TENSE_MARKERS) + r")\b",
    re.IGNORECASE,
)
_PRESENT_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in PRESENT_TENSE_MARKERS) + r")\b",
    re.IGNORECASE,
)

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE_RE = re.compile(r"\s+")
_VOWEL_RE = re.compile(r"[aeiouy]+")
_NON_ALPHA_RE = re.compile(r"[^a-z]")

_FIELDS = ("title", "description", "benefit")


def _field(entry: Any, name: str) -> str:
    """Return a string field of an entry object or mapping."""
    if isinstance(entry, Mapping):
        value = entry.get(name)
    elif isinstance(entry, (str, bytes, bytearray)):
        value = ""
    else:
        value = getattr(entry, name, "")
    if value is None or callable(value):
        return ""
    return value if isinstance(value, str) else str(value)


def _entry_id(entry: Any) -> str:
    return _field(entry, "id")


def _entry_text(entry: Any) -> str:
    """Return the title + description + benefit text of one entry."""
    return " ".join(
        part.strip() for part in (_field(entry, f).strip() for f in _FIELDS) if part
    )


def split_sentences(text: str) -> List[str]:
    """Split ``text`` into sentences with a fixed, deterministic rule.

    Whitespace (including newlines) is collapsed first, then the text is split
    after ``.``/``!``/``?`` followed by whitespace. Text with no terminal
    punctuation stays a single sentence. Always returns a list.
    """
    collapsed = _WHITESPACE_RE.sub(" ", text or "").strip()
    if not collapsed:
        return []
    return [
        part.strip() for part in _SENTENCE_RE.split(collapsed) if part.strip()
    ]


def count_syllables(word: str) -> int:
    """Return a deterministic syllable count for ``word``.

    Vowel groups are counted after a silent-``e`` correction (``make`` = 1,
    ``table`` = 2). Non-letter characters are ignored and every non-empty word
    counts as at least one syllable.
    """
    cleaned = _NON_ALPHA_RE.sub("", str(word).lower())
    if not cleaned:
        return 0
    if len(cleaned) <= 3:
        return 1
    if cleaned.endswith("e") and not cleaned.endswith(("le", "ee", "ye")):
        cleaned = cleaned[:-1]
    groups = _VOWEL_RE.findall(cleaned)
    return max(1, len(groups))


def _preview(text: str, limit: int = 80) -> str:
    collapsed = _WHITESPACE_RE.sub(" ", text or "").strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def _severity(code: str, config: WorkspaceConfig) -> str:
    override = config.severity_overrides.get(code)
    if override in (SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW):
        return override
    return LANGUAGE_SEVERITIES.get(code, SEVERITY_LOW)


def _finding(
    code: str, entry_id: str, message: str, config: WorkspaceConfig
) -> Dict[str, str]:
    return {
        "code": code,
        "severity": _severity(code, config),
        "entry_id": entry_id,
        "message": message,
    }


def _entry_findings(entry: Any, config: WorkspaceConfig) -> List[Dict[str, str]]:
    """Return the per-entry findings for ``entry`` (stable code order)."""
    entry_id = _entry_id(entry)
    text = _entry_text(entry)
    findings: List[Dict[str, str]] = []

    for sentence in split_sentences(text):
        words = _WORD_RE.findall(sentence)
        if len(words) >= LONG_SENTENCE_WORDS:
            findings.append(
                _finding(
                    LONG_SENTENCE,
                    entry_id,
                    "Sentence is {} words (limit {}): {}.".format(
                        len(words), LONG_SENTENCE_WORDS, _preview(sentence)
                    ),
                    config,
                )
            )

    passive = _PASSIVE_RE.findall(text)
    if passive:
        findings.append(
            _finding(
                PASSIVE_VOICE,
                entry_id,
                "Passive voice: {}.".format(
                    ", ".join(sorted({match.lower() for match in passive}))
                ),
                config,
            )
        )

    first_person = _FIRST_PERSON_RE.findall(text)
    if first_person:
        findings.append(
            _finding(
                FIRST_PERSON,
                entry_id,
                "First-person wording: {}.".format(
                    ", ".join(sorted({match.lower() for match in first_person}))
                ),
                config,
            )
        )

    caps = [word for word in _ALL_CAPS_RE.findall(text) if word not in _CAPS_ALLOW]
    if caps:
        findings.append(
            _finding(
                ALL_CAPS,
                entry_id,
                "ALL-CAPS wording: {}.".format(", ".join(sorted(set(caps)))),
                config,
            )
        )

    emoji = _EMOJI_RE.findall(text)
    if emoji:
        findings.append(
            _finding(
                EMOJI,
                entry_id,
                "Emoji in a release note ({} found).".format(len(emoji)),
                config,
            )
        )

    jargon = _JARGON_RE.findall(text)
    if jargon:
        findings.append(
            _finding(
                JARGON,
                entry_id,
                "Jargon: {}.".format(
                    ", ".join(sorted({match.lower() for match in jargon}))
                ),
                config,
            )
        )

    return findings


def _tense_switches(entries: Sequence[Any]) -> int:
    """Return the number of past/present tense transitions in the benefits."""
    sequence: List[str] = []
    for entry in entries:
        benefit = _field(entry, "benefit")
        has_past = bool(_PAST_RE.search(benefit))
        has_present = bool(_PRESENT_RE.search(benefit))
        if has_past and has_present:
            sequence.append("mixed")
        elif has_past:
            sequence.append("past")
        elif has_present:
            sequence.append("present")

    switches = 0
    previous: Optional[str] = None
    for tense in sequence:
        if tense == "mixed":
            switches += 1
            continue
        if previous is not None and previous != tense:
            switches += 1
        previous = tense
    return switches


def _style_counts(entries: Sequence[Any]) -> Dict[str, int]:
    """Return the ``style`` counters (match counts plus tense mix)."""
    counts = {
        PASSIVE_VOICE: 0,
        FIRST_PERSON: 0,
        ALL_CAPS: 0,
        EMOJI: 0,
        JARGON: 0,
    }
    for entry in entries:
        text = _entry_text(entry)
        counts[PASSIVE_VOICE] += len(_PASSIVE_RE.findall(text))
        counts[FIRST_PERSON] += len(_FIRST_PERSON_RE.findall(text))
        counts[ALL_CAPS] += len(
            [w for w in _ALL_CAPS_RE.findall(text) if w not in _CAPS_ALLOW]
        )
        counts[EMOJI] += len(_EMOJI_RE.findall(text))
        counts[JARGON] += len(_JARGON_RE.findall(text))
    return {
        "passive_voice_hits": counts[PASSIVE_VOICE],
        "first_person_hits": counts[FIRST_PERSON],
        "all_caps_hits": counts[ALL_CAPS],
        "emoji_hits": counts[EMOJI],
        "jargon_hits": counts[JARGON],
        "tense_mix": _tense_switches(entries),
    }


def _duplicate_headings(
    entries: Sequence[Any], config: WorkspaceConfig
) -> List[Dict[str, str]]:
    """Return one finding per entry title that appears more than once."""
    seen: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        title = _field(entry, "title").strip()
        if not title:
            continue
        key = _WHITESPACE_RE.sub(" ", title.lower())
        bucket = seen.get(key)
        if bucket is None:
            seen[key] = {"title": title, "count": 1, "entry_id": _entry_id(entry)}
        else:
            bucket["count"] += 1
            bucket["entry_id"] = _entry_id(entry)

    findings: List[Dict[str, str]] = []
    for key in seen:
        bucket = seen[key]
        if bucket["count"] > 1:
            findings.append(
                _finding(
                    DUPLICATE_HEADING,
                    bucket["entry_id"],
                    "Duplicate heading {} appears {} times.".format(
                        bucket["title"], bucket["count"]
                    ),
                    config,
                )
            )
    return findings


def _readability(entries: Sequence[Any]) -> Dict[str, float]:
    """Return the readability metrics over all entry text."""
    draft_text = " ".join(_entry_text(entry) for entry in entries)
    sentences = split_sentences(draft_text)
    words: List[str] = []
    for sentence in sentences:
        words.extend(_WORD_RE.findall(sentence))

    total_words = len(words)
    total_sentences = len(sentences)
    long_sentences = [
        sentence
        for sentence in sentences
        if len(_WORD_RE.findall(sentence)) >= LONG_SENTENCE_WORDS
    ]

    if total_sentences:
        avg_sentence_length = round(total_words / total_sentences, 2)
    else:
        avg_sentence_length = 0.0

    if total_words:
        syllables = sum(count_syllables(word) for word in words)
        avg_syllables = round(syllables / total_words, 2)
    else:
        avg_syllables = 0.0

    if total_sentences:
        long_ratio = round(len(long_sentences) / total_sentences, 2)
    else:
        long_ratio = 0.0

    if total_words:
        flesch = 206.835 - 1.015 * avg_sentence_length - 84.6 * avg_syllables
        flesch = round(flesch, 2)
    else:
        flesch = 0.0

    return {
        "flesch_reading_ease": flesch,
        "avg_sentence_length": avg_sentence_length,
        "avg_syllables_per_word": avg_syllables,
        "long_sentence_ratio": long_ratio,
    }


def _grade(counts: Dict[str, int]) -> str:
    total = counts["total"]
    if total == 0:
        return GRADE_CLEAR
    if total <= ACCEPTABLE_FINDING_LIMIT:
        return GRADE_ACCEPTABLE
    return GRADE_DENSE


def compute_language(
    entries: Any,
    template: Any = None,
    config: Optional[WorkspaceConfig] = None,
) -> Dict[str, Any]:
    """Return the deterministic language report for ``entries``.

    ``entries`` is a sequence of :class:`~releasenote.models.ChangeEntry`
    objects (or mappings). ``config`` supplies optional ``severity_overrides``.
    ``template`` is accepted for signature parity with the rest of the pipeline
    and does not change the result.

    The result is a JSON-serialisable mapping with four keys:

    * ``readability`` — ``flesch_reading_ease``, ``avg_sentence_length``,
      ``avg_syllables_per_word`` and ``long_sentence_ratio``;
    * ``style`` — ``passive_voice_hits``, ``first_person_hits``,
      ``all_caps_hits``, ``emoji_hits``, ``jargon_hits`` and ``tense_mix``;
    * ``findings`` — a list of ``{code, severity, entry_id, message}``;
    * ``summary`` — ``{grade, counts: {high, medium, low, total}}``.

    A falsy or malformed ``entries`` value never raises: it yields zero
    findings and a ``clear`` grade.
    """
    config = config or WorkspaceConfig.default()
    items: List[Any] = list(entries) if isinstance(entries, (list, tuple)) else []
    if entries is not None and not isinstance(entries, (list, tuple)):
        # Any other non-None iterable (e.g. a generator) is tolerated.
        try:
            items = list(entries)
        except TypeError:
            items = []

    findings: List[Dict[str, str]] = []
    for entry in items:
        findings.extend(_entry_findings(entry, config))

    tense_switches = _tense_switches(items)
    if tense_switches > 0:
        findings.append(
            _finding(
                TENSE_INCONSISTENCY,
                "",
                "Benefits mix past and present tense ({} switch{}).".format(
                    tense_switches, "" if tense_switches == 1 else "es"
                ),
                config,
            )
        )

    findings.extend(_duplicate_headings(items, config))

    counts = {"high": 0, "medium": 0, "low": 0, "total": len(findings)}
    for finding in findings:
        severity = finding["severity"]
        if severity in counts:
            counts[severity] += 1

    return {
        "readability": _readability(items),
        "style": _style_counts(items),
        "findings": findings,
        "summary": {"grade": _grade(counts), "counts": counts},
    }


def language_flags(
    entries: Any,
    template: Any = None,
    config: Optional[WorkspaceConfig] = None,
) -> List[Flag]:
    """Return the language findings as :class:`~releasenote.models.Flag`s."""
    report = compute_language(entries, template, config)
    return [
        Flag(
            code=finding["code"],
            severity=finding["severity"],
            message=finding["message"],
            entry_id=finding["entry_id"],
        )
        for finding in report["findings"]
    ]


__all__ = [
    "LONG_SENTENCE",
    "PASSIVE_VOICE",
    "FIRST_PERSON",
    "ALL_CAPS",
    "EMOJI",
    "JARGON",
    "TENSE_INCONSISTENCY",
    "DUPLICATE_HEADING",
    "LANGUAGE_FLAG_CODES",
    "LANGUAGE_SEVERITIES",
    "LONG_SENTENCE_WORDS",
    "ACCEPTABLE_FINDING_LIMIT",
    "GRADE_CLEAR",
    "GRADE_ACCEPTABLE",
    "GRADE_DENSE",
    "JARGON_TERMS",
    "PAST_TENSE_MARKERS",
    "PRESENT_TENSE_MARKERS",
    "split_sentences",
    "count_syllables",
    "compute_language",
    "language_flags",
]
