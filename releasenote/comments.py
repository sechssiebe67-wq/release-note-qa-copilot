"""Pure, dependency-free helpers for per-entry review discussion.

Reviewers need to talk about a *specific claim* in a draft instead of leaving
feedback in a separate chat or e-mail. This module models that discussion as an
append-only, JSON-serialisable **thread** of comments: each comment is attached
to an entry (so it stays next to the claim it is about), may reply to another
comment through ``parent_id`` and carries a boolean ``resolved`` flag.

Everything here is pure: the functions never touch the network, the filesystem
or the clock on their own, and they always return a **new** thread rather than
mutating the one they are given. Comment ids are generated deterministically
from the existing thread (``c1``, ``c2``, …), so replaying the same operations
yields the same result.

A thread is a plain mapping ``{"draft_id": Optional[str], "comments": [...]}``
where every comment is a mapping with the keys ``id``, ``entry_id``,
``parent_id``, ``author``, ``body``, ``created_at`` and ``resolved``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

# A comment body is trimmed and must be non-empty and at most this long.
MAX_COMMENT_LENGTH = 4096
# Authors are optional; an empty or missing author becomes this.
ANONYMOUS_AUTHOR = "anonymous"


def _timestamp(now: Any) -> str:
    """Normalise a ``now`` value into a deterministic ISO-8601 string.

    ``None`` yields an empty string so the function stays pure and
    deterministic (the durable store always supplies a real timestamp), a
    :class:`datetime` is converted to UTC and a string is passed through
    unchanged. Any other type raises :class:`ValueError`.
    """
    if now is None:
        return ""
    if isinstance(now, datetime):
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc).isoformat()
    if isinstance(now, str):
        return now
    raise ValueError("'now' must be an ISO-8601 string or a datetime")


def _thread_comments(thread: Any) -> List[Dict[str, Any]]:
    """Return the comment list of ``thread`` (an empty list when absent)."""
    if isinstance(thread, Mapping):
        comments = thread.get("comments")
    elif isinstance(thread, list):
        comments = thread
    else:
        comments = None
    if not isinstance(comments, list):
        return []
    return [dict(item) for item in comments if isinstance(item, Mapping)]


def _draft_id(thread: Any) -> Optional[str]:
    if isinstance(thread, Mapping):
        draft_id = thread.get("draft_id")
        if draft_id is None or isinstance(draft_id, str):
            return draft_id
    return None


def validate_entry_id(entry_id: Any) -> str:
    """Return a trimmed, non-empty entry id or raise :class:`ValueError`."""
    if not isinstance(entry_id, str):
        raise ValueError("entry_id must be a string")
    trimmed = entry_id.strip()
    if not trimmed:
        raise ValueError("entry_id is required")
    return trimmed


def validate_comment_body(body: Any) -> str:
    """Return the trimmed comment body or raise :class:`ValueError`.

    The body must be a string, non-empty after trimming and no longer than
    :data:`MAX_COMMENT_LENGTH` characters.
    """
    if not isinstance(body, str):
        raise ValueError("comment body must be a string")
    trimmed = body.strip()
    if not trimmed:
        raise ValueError("comment body must not be empty")
    if len(trimmed) > MAX_COMMENT_LENGTH:
        raise ValueError(
            "comment body must be at most {} characters".format(
                MAX_COMMENT_LENGTH
            )
        )
    return trimmed


def normalize_author(author: Any) -> str:
    """Return a trimmed author name, defaulting to ``anonymous``.

    ``None`` and an empty (or whitespace-only) string both map to
    :data:`ANONYMOUS_AUTHOR`. A non-string author raises :class:`ValueError`.
    """
    if author is None:
        return ANONYMOUS_AUTHOR
    if not isinstance(author, str):
        raise ValueError("author must be a string")
    trimmed = author.strip()
    return trimmed or ANONYMOUS_AUTHOR


def _next_comment_id(comments: List[Mapping[str, Any]]) -> str:
    """Return the next deterministic id (``c1``, ``c2``, …) for ``comments``."""
    highest = 0
    for comment in comments:
        identifier = comment.get("id")
        if isinstance(identifier, str) and identifier.startswith("c"):
            suffix = identifier[1:]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return "c{}".format(highest + 1)


def _parent_id(comments: List[Mapping[str, Any]], parent_id: Any) -> Optional[str]:
    """Validate ``parent_id`` against ``comments`` and return it (or ``None``)."""
    if parent_id is None:
        return None
    if not isinstance(parent_id, str) or not parent_id.strip():
        raise ValueError("parent_id must be a non-empty string")
    normalized = parent_id.strip()
    if not any(comment.get("id") == normalized for comment in comments):
        raise LookupError("unknown parent comment id: {}".format(normalized))
    return normalized


def add_comment(
    thread: Any,
    entry_id: Any,
    author: Any,
    body: Any,
    parent_id: Any = None,
    now: Any = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return ``(new_thread, comment)`` with one comment added.

    The input ``thread`` is never mutated. The new comment is appended in
    creation order with a deterministic id. ``parent_id`` must reference an
    existing comment in the same thread, otherwise :class:`LookupError` is
    raised and nothing is created.
    """
    comments = _thread_comments(thread)
    entry = validate_entry_id(entry_id)
    text = validate_comment_body(body)
    who = normalize_author(author)
    parent = _parent_id(comments, parent_id)
    comment = {
        "id": _next_comment_id(comments),
        "entry_id": entry,
        "parent_id": parent,
        "author": who,
        "body": text,
        "created_at": _timestamp(now),
        "resolved": False,
    }
    new_thread = {
        "draft_id": _draft_id(thread),
        "comments": comments + [comment],
    }
    return new_thread, dict(comment)


def resolve_comment(thread: Any, comment_id: Any, resolved: Any) -> Dict[str, Any]:
    """Return a new thread with ``comment_id``'s ``resolved`` flag set.

    The operation is idempotent: setting an already-resolved comment to
    ``True`` again is a no-op apart from returning a fresh thread. An unknown
    comment id raises :class:`LookupError` and a non-boolean ``resolved``
    raises :class:`ValueError`.
    """
    if not isinstance(comment_id, str) or not comment_id.strip():
        raise ValueError("comment_id is required")
    if not isinstance(resolved, bool):
        raise ValueError("'resolved' must be a boolean")
    target = comment_id.strip()
    comments = _thread_comments(thread)
    found = False
    for comment in comments:
        if comment.get("id") == target:
            comment["resolved"] = resolved
            found = True
    if not found:
        raise LookupError("unknown comment id: {}".format(target))
    return {"draft_id": _draft_id(thread), "comments": comments}


def summarize_thread(thread: Any) -> Dict[str, Any]:
    """Return per-entry comment counts and overall totals for ``thread``.

    The result is::

        {
          "total": 3,
          "unresolved": 2,
          "resolved": 1,
          "entries": {
            "REL-1": {"total": 2, "unresolved": 1, "resolved": 1,
                      "participants": ["Ada", "Bob"]}
          }
        }

    ``entries`` keeps first-creation order and ``participants`` keeps
    first-appearance order, so the summary is deterministic.
    """
    comments = _thread_comments(thread)
    entries: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for comment in comments:
        entry_id = str(comment.get("entry_id") or "")
        if entry_id not in entries:
            entries[entry_id] = {
                "total": 0,
                "unresolved": 0,
                "resolved": 0,
                "participants": [],
            }
            order.append(entry_id)
        row = entries[entry_id]
        row["total"] += 1
        if comment.get("resolved"):
            row["resolved"] += 1
        else:
            row["unresolved"] += 1
        author = comment.get("author") or ANONYMOUS_AUTHOR
        if author not in row["participants"]:
            row["participants"].append(author)
    total = len(comments)
    unresolved = sum(1 for comment in comments if not comment.get("resolved"))
    return {
        "total": total,
        "unresolved": unresolved,
        "resolved": total - unresolved,
        "entries": {entry_id: entries[entry_id] for entry_id in order},
    }


__all__ = [
    "ANONYMOUS_AUTHOR",
    "MAX_COMMENT_LENGTH",
    "add_comment",
    "normalize_author",
    "resolve_comment",
    "summarize_thread",
    "validate_comment_body",
    "validate_entry_id",
]
