"""Minimal HTTP server for the Release Note QA Copilot.

Only the Python standard library is used so the container stays small and
dependency free. The server exposes a small JSON API plus a browser UI.
"""

from __future__ import annotations

import html
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import WorkspaceConfig
from .exporters import (
    checklist_csv,
    comparison_csv,
    comparison_json,
    comparison_matrix_csv,
    diff_csv,
    draft_json,
    profile_comparison_csv,
    profile_comparison_json,
    qa_flags_csv,
    rules_csv,
    rules_json,
    template_csv,
    template_json,
    trend_csv,
    trend_json,
)
from .importer import ParseError
from .profiles import (
    UnknownProfileError,
    list_profiles,
    normalize_profile_name,
    resolve_config,
)
from .templates import (
    UnknownTemplateError,
    list_templates,
    resolve_template,
    resolve_template_from_payload,
)
from .service import (
    HistoryRequiredError,
    build_comparison,
    build_diff,
    build_draft,
    build_profile_comparison,
    build_trend,
    get_review,
    get_shared_draft,
    record_review,
    sample,
    sample_comparison,
    sample_profile_comparison,
    sample_trend,
    sample_workspace_config,
)
from .storage import DraftStore, ReviewStore

MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MiB upload cap.
MAX_DRAIN_BYTES = 8 * 1024 * 1024  # Bounded read of an over-limit body.
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Export endpoints and the (mime type, filename) each one serves.
_EXPORTS = {
    "/api/export/qa.csv": ("text/csv", "qa-flags.csv"),
    "/api/export/checklist.csv": ("text/csv", "qa-checklist.csv"),
    "/api/export/diff.csv": ("text/csv", "release-diff.csv"),
    "/api/export/rules.csv": ("text/csv", "qa-rules.csv"),
    "/api/export/rules.json": ("application/json; charset=utf-8", "qa-rules.json"),
    "/api/export/template.csv": ("text/csv", "release-template.csv"),
    "/api/export/template.json": (
        "application/json; charset=utf-8",
        "release-template.json",
    ),
    "/api/export/draft.json": ("application/json; charset=utf-8", "release-draft.json"),
}

# Exports whose query contract accepts ``previous``/``current`` (a diff) as
# well as ``content`` (a single draft).
_TEMPLATE_EXPORTS = ("/api/export/template.csv", "/api/export/template.json")

# Trend exports built from repeated ``release=label:content`` query params.
_TREND_EXPORTS = {
    "/api/export/trend.csv": ("text/csv", "release-trend.csv"),
    "/api/export/trend.json": (
        "application/json; charset=utf-8",
        "release-trend.json",
    ),
}

# Comparison exports built from repeated ``draft=label:content`` query params.
_COMPARE_EXPORTS = {
    "/api/export/comparison.csv": ("text/csv", "draft-comparison.csv"),
    "/api/export/comparison.json": (
        "application/json; charset=utf-8",
        "draft-comparison.json",
    ),
    "/api/export/comparison-matrix.csv": (
        "text/csv",
        "draft-comparison-matrix.csv",
    ),
}
# Profile-comparison exports built from ``format``/``content`` plus an optional
# repeated ``profile`` and inline ``config`` query parameter.
_PROFILE_EXPORTS = {
    "/api/export/profile-comparison.csv": (
        "text/csv",
        "profile-comparison.csv",
    ),
    "/api/export/profile-comparison.json": (
        "application/json; charset=utf-8",
        "profile-comparison.json",
    ),
}
_FORMATS = ("auto", "csv", "json")


def _first(query: Dict[str, list], key: str, default: Optional[str] = None) -> Optional[str]:
    """Return the first value for ``key`` in a parsed query string."""
    values = query.get(key)
    if not values:
        return default
    return values[0]


def _read_static(name: str) -> bytes:
    path = os.path.join(STATIC_DIR, name)
    with open(path, "rb") as handle:
        return handle.read()


# Inline styling for the read-only share page. No external stylesheet or
# script is referenced so the page is fully self-contained and dependency free.
_SHARE_CSS = """
:root { color-scheme: light dark; --bg:#0f172a; --panel:#1e293b; --text:#e2e8f0;
  --muted:#94a3b8; --accent:#38bdf8; --high:#f87171; --medium:#fbbf24;
  --pass:#34d399; --border:#334155; }
* { box-sizing: border-box; }
body { margin:0; font-family: ui-sans-serif, system-ui, -apple-system,
  "Segoe UI", Roboto, sans-serif; background:var(--bg); color:var(--text);
  line-height:1.5; }
main { max-width:960px; margin:0 auto; padding:1.5rem 1.25rem 3rem; }
h1 { margin:0 0 .25rem; font-size:1.5rem; }
.summary { display:flex; gap:.5rem; flex-wrap:wrap; margin:.75rem 0; }
.pill { padding:.2rem .6rem; border-radius:999px; font-size:.8rem;
  border:1px solid var(--border); }
.pill.high, .pill.rejected { color:var(--high); border-color:var(--high); }
.pill.medium, .pill.pending { color:var(--medium); border-color:var(--medium); }
.pill.pass, .pill.approved { color:var(--pass); border-color:var(--pass); }
pre { background:#0b1220; border:1px solid var(--border); border-radius:8px;
  padding:.8rem; overflow:auto; font-size:.82rem; white-space:pre-wrap;
  word-break:break-word; }
a { color:var(--accent); }
.muted { color:var(--muted); font-size:.85rem; }
""".strip()


def _share_badge_class(value: str) -> str:
    if value in ("high", "rejected"):
        return "high"
    if value in ("medium", "pending"):
        return "medium"
    return "pass"


def _render_share_html(payload: Dict[str, Any]) -> str:
    """Render a stored draft as a small, self-contained read-only HTML page.

    The title, Markdown, QA counts and review badge are HTML-escaped before
    insertion, so untrusted draft content cannot inject markup. No external
    stylesheet or script is referenced.
    """
    title = html.escape(str(payload.get("title") or "Release Notes"))
    markdown = html.escape(str(payload.get("markdown") or ""))
    qa = payload.get("qa") or {}
    counts = qa.get("counts") or {}
    rules = payload.get("rules") or {}
    publishable = bool(qa.get("publishable"))
    review = payload.get("review") or {}
    status = str(review.get("status") or "pending")
    draft_id = html.escape(str(payload.get("draft_id") or ""))
    reviewer = html.escape(str(review.get("reviewer") or ""))

    publish_pill = '<span class="pill {}">{}</span>'.format(
        "pass" if publishable else "high",
        "publishable" if publishable else "not publishable",
    )
    review_bits = '<span class="pill {}">review: {}</span>'.format(
        _share_badge_class(status), html.escape(status)
    )
    if reviewer:
        review_bits += '<span class="pill">reviewer: {}</span>'.format(reviewer)

    entry_count = rules.get("entry_count", 0)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        "<title>{title} - Release Note QA Copilot</title>\n"
        "<style>\n{css}\n</style>\n</head>\n<body>\n<main>\n"
        "<h1>{title}</h1>\n"
        '<p class="muted">Read-only shared draft <code>{draft_id}</code>. '
        "This link is unauthenticated.</p>\n"
        '<div class="summary">'
        '<span class="pill">{entry_count} entries</span>{publish_pill}'
        '<span class="pill high">{high} high</span>'
        '<span class="pill medium">{medium} medium</span>'
        '<span class="pill">{total} flags</span>{review_bits}'
        "</div>\n"
        "<h2>Draft</h2>\n<pre>{markdown}</pre>\n"
        '<p><a href="/">Back to Release Note QA Copilot</a></p>\n'
        "</main>\n</body>\n</html>\n"
    ).format(
        title=title,
        css=_SHARE_CSS,
        draft_id=draft_id,
        entry_count=html.escape(str(entry_count)),
        publish_pill=publish_pill,
        high=html.escape(str(counts.get("high", 0))),
        medium=html.escape(str(counts.get("medium", 0))),
        total=html.escape(str(counts.get("total", 0))),
        review_bits=review_bits,
        markdown=markdown,
    )


def _render_share_error(message: str) -> str:
    """Render a minimal HTML error page for an unavailable share link."""
    title = "Share link unavailable"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        "<title>{title}</title>\n<style>\n{css}\n</style>\n</head>\n<body>\n"
        '<main>\n<h1>{title}</h1>\n<p class="muted">{message}</p>\n'
        '<p><a href="/">Back to Release Note QA Copilot</a></p>\n'
        "</main>\n</body>\n</html>\n"
    ).format(
        title=title,
        css=_SHARE_CSS,
        message=html.escape(str(message)),
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "ReleaseNoteQACopilot/" + __version__
    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------
    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str = "application/json; charset=utf-8",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ParseError("Invalid Content-Length")
        if length <= 0:
            return b""
        if length > MAX_BODY_BYTES:
            self._drain_body(length)
            raise ParseError("Request body exceeds 2 MiB limit")
        return self.rfile.read(length)

    def _drain_body(self, length: int) -> None:
        """Read up to a bounded amount of an over-limit body.

        Draining lets the client finish writing before the 413 response is
        sent, which avoids a connection reset while still refusing to buffer
        arbitrarily large payloads.
        """
        remaining = min(length, MAX_DRAIN_BYTES)
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        sys.stderr.write(
            "%s - %s\n" % (self.address_string(), fmt % args)
        )

    # -- routing ---------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path in ("/", "/index.html"):
            try:
                html = _read_static("index.html")
            except OSError:
                self._send(500, b"UI unavailable", "text/plain; charset=utf-8")
                return
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path in ("/healthz", "/readyz"):
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return
        if path.startswith("/s/"):
            self._handle_share_html(path[len("/s/"):])
            return
        if path == "/api/version":
            self._send_json(200, {"name": "release-note-qa-copilot", "version": __version__})
            return
        if path == "/api/sample":
            query = parse_qs(parsed.query)
            fmt = (query.get("format") or ["csv"])[0]
            self._send_json(200, sample(fmt))
            return
        if path == "/api/config/sample":
            self._send_json(200, sample_workspace_config())
            return
        if path == "/api/profiles":
            self._send_json(200, {"profiles": list_profiles()})
            return
        if path == "/api/templates":
            self._send_json(200, {"templates": list_templates()})
            return
        if path == "/api/trend/sample":
            self._send_json(200, sample_trend())
            return
        if path == "/api/compare/sample":
            self._send_json(200, sample_comparison())
            return
        if path == "/api/compare-profiles/sample":
            self._send_json(200, sample_profile_comparison())
            return
        if path == "/api/history":
            store = getattr(self.server, "store", None)
            query = parse_qs(parsed.query)
            try:
                limit = int((query.get("limit") or ["50"])[0])
            except ValueError:
                limit = 50
            self._send_json(200, {"drafts": store.list(limit) if store else []})
            return
        if path.startswith("/api/history/"):
            draft_id = path[len("/api/history/"):]
            store = getattr(self.server, "store", None)
            record = store.get(draft_id) if store else None
            if record is None:
                self._send_json(404, {"error": "draft not found"})
            else:
                self._send_json(200, record)
            return
        if path.startswith("/api/review/"):
            draft_id = path[len("/api/review/"):]
            review_store = getattr(self.server, "review_store", None)
            try:
                payload = get_review(draft_id, review_store)
            except LookupError as exc:
                self._send_json(404, {"error": str(exc)})
                return
            self._send_json(200, payload)
            return
        if path.startswith("/api/share/"):
            draft_id = path[len("/api/share/"):]
            payload = self._shared_draft(draft_id, json_errors=True)
            if payload is not None:
                self._send_json(200, payload)
            return
        if path in _PROFILE_EXPORTS:
            self._handle_profile_export(path, parsed)
            return
        if path in _TREND_EXPORTS:
            self._handle_trend_export(path, parsed)
            return
        if path in _COMPARE_EXPORTS:
            self._handle_compare_export(path, parsed)
            return
        if path in _EXPORTS:
            self._handle_export(path, parsed)
            return
        self._send_json(404, {"error": "not found", "path": parsed.path})

    def _handle_trend_export(self, path: str, parsed: Any) -> None:
        """Serve a trend CSV/JSON export built from query parameters.

        Each repeated ``release`` parameter is ``label:content`` (URL-encoded),
        in release order; an optional ``format`` applies to every release and
        an optional ``profile`` selects a named rule profile. Missing or
        invalid parameters return 400, an unknown profile returns 400 naming
        it and unparseable content returns 422.
        """
        query = parse_qs(parsed.query)
        fmt = _first(query, "format", "auto")
        if fmt not in _FORMATS:
            self._send_json(400, {"error": "unsupported format: {!r}".format(fmt)})
            return
        raw_releases = query.get("release") or []
        releases = []
        for value in raw_releases:
            label, separator, content = value.partition(":")
            if not separator or not content.strip():
                self._send_json(
                    400, {"error": "each 'release' must be 'label:content'"}
                )
                return
            releases.append(
                {"label": label.strip(), "content": content, "format": fmt}
            )
        payload = {
            "releases": releases,
            "profile": _first(query, "profile"),
        }
        try:
            report = build_trend(payload)
        except UnknownProfileError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except ParseError as exc:
            self._send_json(422, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        body = trend_csv(report) if path.endswith(".csv") else trend_json(report)
        content_type, filename = _TREND_EXPORTS[path]
        self._send(
            200,
            body.encode("utf-8"),
            content_type,
            {"Content-Disposition": 'attachment; filename="{}"'.format(filename)},
        )

    def _handle_compare_export(self, path: str, parsed: Any) -> None:
        """Serve a comparison CSV/JSON export built from query parameters.

        Each repeated ``draft`` parameter is ``label:content`` (URL-encoded);
        an optional ``format`` applies to every draft and an optional
        ``profile`` selects a named rule profile. Missing or invalid parameters
        return 400, an unknown profile returns 400 naming it and unparseable
        content returns 422.
        """
        query = parse_qs(parsed.query)
        fmt = _first(query, "format", "auto")
        if fmt not in _FORMATS:
            self._send_json(400, {"error": "unsupported format: {!r}".format(fmt)})
            return
        raw_drafts = query.get("draft") or []
        drafts = []
        for value in raw_drafts:
            label, separator, content = value.partition(":")
            if not separator or not content.strip():
                self._send_json(
                    400, {"error": "each 'draft' must be 'label:content'"}
                )
                return
            drafts.append(
                {"label": label.strip(), "content": content, "format": fmt}
            )
        payload = {
            "drafts": drafts,
            "profile": _first(query, "profile"),
        }
        try:
            report = build_comparison(payload)
        except UnknownProfileError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except ParseError as exc:
            self._send_json(422, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        if path == "/api/export/comparison.csv":
            body = comparison_csv(report)
        elif path == "/api/export/comparison-matrix.csv":
            body = comparison_matrix_csv(report)
        else:
            body = comparison_json(report)
        content_type, filename = _COMPARE_EXPORTS[path]
        self._send(
            200,
            body.encode("utf-8"),
            content_type,
            {"Content-Disposition": 'attachment; filename="{}"'.format(filename)},
        )

    def _handle_profile_export(self, path: str, parsed: Any) -> None:
        """Serve a profile-comparison CSV/JSON export from query parameters.

        ``format`` and ``content`` are required; optional repeated ``profile``
        parameters name the profiles to compare (default: every built-in) and
        an optional ``config`` query parameter is inline JSON layered on top of
        each profile. Missing or invalid parameters return 400, an unknown
        profile returns 400 naming it and unparseable content returns 422.
        """
        query = parse_qs(parsed.query)
        fmt = _first(query, "format", "auto")
        if fmt not in _FORMATS:
            self._send_json(400, {"error": "unsupported format: {!r}".format(fmt)})
            return
        content = _first(query, "content")
        if not content or not content.strip():
            self._send_json(400, {"error": "query 'content' is required"})
            return
        payload: Dict[str, Any] = {"content": content, "format": fmt}
        profiles = query.get("profile")
        if profiles:
            payload["profiles"] = profiles
        config_raw = _first(query, "config")
        if config_raw:
            try:
                payload["config"] = json.loads(config_raw)
            except json.JSONDecodeError:
                self._send_json(
                    400, {"error": "query 'config' must be valid JSON"}
                )
                return
        try:
            report = build_profile_comparison(payload)
        except UnknownProfileError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except ParseError as exc:
            self._send_json(422, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        body = (
            profile_comparison_csv(report)
            if path.endswith(".csv")
            else profile_comparison_json(report)
        )
        content_type, filename = _PROFILE_EXPORTS[path]
        self._send(
            200,
            body.encode("utf-8"),
            content_type,
            {"Content-Disposition": 'attachment; filename="{}"'.format(filename)},
        )

    def _handle_export(self, path: str, parsed: Any) -> None:
        """Serve a CSV or JSON export built from query parameters.

        The endpoints mirror ``/api/generate`` and ``/api/diff``: ``format``,
        ``title``, ``content``, ``profile`` and ``template`` (plus
        ``previous``/``current`` for a diff) are accepted as query parameters.
        The template exports accept either a single draft or a diff. Missing or
        invalid parameters return 400, while unparseable content returns 422.
        """
        query = parse_qs(parsed.query)
        fmt = _first(query, "format", "auto")
        title = _first(query, "title", "Release Notes")
        if fmt not in _FORMATS:
            self._send_json(400, {"error": "unsupported format: {!r}".format(fmt)})
            return
        profile_name = _first(query, "profile")
        try:
            profile_name = normalize_profile_name(profile_name)
            workspace = resolve_config({"profile": profile_name})
        except UnknownProfileError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": "invalid config: {}".format(exc)})
            return
        try:
            template = resolve_template(_first(query, "template"))
        except UnknownTemplateError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": "invalid template: {}".format(exc)})
            return
        try:
            if path == "/api/export/diff.csv":
                previous = _first(query, "previous")
                current = _first(query, "current")
                if not previous or not previous.strip():
                    self._send_json(400, {"error": "query 'previous' is required"})
                    return
                if not current or not current.strip():
                    self._send_json(400, {"error": "query 'current' is required"})
                    return
                result = build_diff(
                    previous, current, fmt, title, workspace, profile_name,
                    template,
                )
                body = diff_csv(result)
            elif path in _TEMPLATE_EXPORTS:
                previous = _first(query, "previous")
                current = _first(query, "current")
                if previous is not None and current is not None:
                    if not previous.strip() or not current.strip():
                        self._send_json(
                            400, {"error": "query 'previous' and 'current' are required"}
                        )
                        return
                    result = build_diff(
                        previous, current, fmt, title, workspace, profile_name,
                        template,
                    )
                else:
                    content = _first(query, "content")
                    if not content or not content.strip():
                        self._send_json(
                            400, {"error": "query 'content' is required"}
                        )
                        return
                    result = build_draft(
                        content, fmt, title, workspace, profile_name, template
                    )
                body = (
                    template_csv(result)
                    if path.endswith(".csv")
                    else template_json(result)
                )
            else:
                content = _first(query, "content")
                if not content or not content.strip():
                    self._send_json(400, {"error": "query 'content' is required"})
                    return
                result = build_draft(
                    content, fmt, title, workspace, profile_name, template
                )
                if path == "/api/export/qa.csv":
                    body = qa_flags_csv(result)
                elif path == "/api/export/checklist.csv":
                    body = checklist_csv(result)
                elif path == "/api/export/rules.csv":
                    body = rules_csv(result)
                elif path == "/api/export/rules.json":
                    body = rules_json(result)
                else:
                    body = draft_json(result)
        except ParseError as exc:
            self._send_json(422, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": "invalid config: {}".format(exc)})
            return
        content_type, filename = _EXPORTS[path]
        self._send(
            200,
            body.encode("utf-8"),
            content_type,
            {"Content-Disposition": 'attachment; filename="{}"'.format(filename)},
        )

    def _shared_draft(
        self, draft_id: str, json_errors: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Load a stored draft for sharing, sending an error response on failure.

        Returns the JSON-serialisable payload, or ``None`` after a response was
        sent. An unset history store is reported as HTTP 400 (share links need
        ``DATA_DIR``) and an unknown id as HTTP 404. Reading never writes to
        disk.
        """
        store = getattr(self.server, "store", None)
        review_store = getattr(self.server, "review_store", None)
        try:
            return get_shared_draft(draft_id, store, review_store)
        except HistoryRequiredError as exc:
            if json_errors:
                self._send_json(400, {"error": str(exc)})
            else:
                self._send(
                    400,
                    _render_share_error(str(exc)).encode("utf-8"),
                    "text/html; charset=utf-8",
                )
        except LookupError as exc:
            if json_errors:
                self._send_json(404, {"error": str(exc)})
            else:
                self._send(
                    404,
                    _render_share_error(str(exc)).encode("utf-8"),
                    "text/html; charset=utf-8",
                )
        return None

    def _handle_share_html(self, draft_id: str) -> None:
        """Serve the read-only HTML share page for a stored draft."""
        payload = self._shared_draft(draft_id, json_errors=False)
        if payload is None:
            return
        self._send(
            200,
            _render_share_html(payload).encode("utf-8"),
            "text/html; charset=utf-8",
        )

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def _store(self, result: Dict[str, Any]) -> None:
        """Persist a generated draft when history is enabled."""
        store = getattr(self.server, "store", None)
        if store is None:
            return
        record = store.save(
            result["title"],
            result["markdown"],
            result["entry_count"],
            result["qa"]["counts"]["high"],
            entries=result.get("entries"),
            config=result.get("config"),
            profile=result.get("profile"),
            template=result.get("template"),
        )
        result["draft_id"] = record["id"]

    def _parse_body(self) -> Optional[Dict[str, Any]]:
        """Read and JSON-decode the request body, replying on error.

        Returns the payload mapping or ``None`` when a response was already
        sent for an invalid, oversized or non-object body.
        """
        try:
            raw = self._read_body()
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "body must be valid JSON"})
            return None
        except ParseError as exc:
            self._send_json(413, {"error": str(exc)})
            return None
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "body must be a JSON object"})
            return None
        return payload

    def _resolve_request(
        self, payload: Dict[str, Any]
    ) -> Optional[Tuple[WorkspaceConfig, Optional[str], Any]]:
        """Resolve the optional ``profile``/``config``/``template`` fields.

        Returns ``(workspace, profile_name, template)`` where ``profile_name``
        is the resolved profile or ``None`` and ``template`` is the resolved
        :class:`~releasenote.templates.ReleaseTemplate`. On error a 400
        response is sent and ``None`` is returned; an unknown profile or
        template names the offending value in the message.
        """
        try:
            profile_name = normalize_profile_name(payload.get("profile"))
            workspace = resolve_config(payload)
        except UnknownProfileError as exc:
            self._send_json(400, {"error": str(exc)})
            return None
        except ValueError as exc:
            self._send_json(400, {"error": "invalid config: {}".format(exc)})
            return None
        try:
            template = resolve_template_from_payload(payload)
        except UnknownTemplateError as exc:
            self._send_json(400, {"error": str(exc)})
            return None
        except ValueError as exc:
            self._send_json(400, {"error": "invalid template: {}".format(exc)})
            return None
        return workspace, profile_name, template

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path not in (
            "/api/generate",
            "/api/diff",
            "/api/review",
            "/api/trend",
            "/api/compare",
            "/api/compare-profiles",
        ):
            self._send_json(404, {"error": "not found", "path": parsed.path})
            return
        payload = self._parse_body()
        if payload is None:
            return
        if path == "/api/review":
            self._post_review(payload)
            return
        if path == "/api/trend":
            self._post_trend(payload)
            return
        if path == "/api/compare":
            self._post_compare(payload)
            return
        if path == "/api/compare-profiles":
            self._post_compare_profiles(payload)
            return
        resolved = self._resolve_request(payload)
        if resolved is None:
            return
        workspace, profile_name, template = resolved
        if path == "/api/generate":
            self._post_generate(payload, workspace, profile_name, template)
        else:
            self._post_diff(payload, workspace, profile_name, template)

    def _post_review(self, payload: Dict[str, Any]) -> None:
        draft_id = payload.get("draft_id")
        if not isinstance(draft_id, str) or not draft_id.strip():
            self._send_json(400, {"error": "field 'draft_id' is required"})
            return
        status = payload.get("status")
        if not isinstance(status, str) or not status.strip():
            self._send_json(400, {"error": "field 'status' is required"})
            return
        reviewer = payload.get("reviewer", "")
        comment = payload.get("comment", "")
        if not isinstance(reviewer, str) or not isinstance(comment, str):
            self._send_json(
                400, {"error": "'reviewer' and 'comment' must be strings"}
            )
            return
        review_store = getattr(self.server, "review_store", None)
        try:
            record = record_review(
                draft_id, status, reviewer, comment, review_store
            )
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except LookupError as exc:
            self._send_json(404, {"error": str(exc)})
            return
        result = record.to_dict()
        result["draft_id"] = result.pop("id")
        audit = (
            review_store.audit(draft_id) if review_store is not None else []
        )
        result["audit"] = audit
        self._send_json(200, result)

    def _post_trend(self, payload: Dict[str, Any]) -> None:
        try:
            result = build_trend(payload)
        except UnknownProfileError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except ParseError as exc:
            self._send_json(422, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, result)

    def _post_compare(self, payload: Dict[str, Any]) -> None:
        try:
            result = build_comparison(payload)
        except UnknownProfileError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except ParseError as exc:
            self._send_json(422, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, result)

    def _post_compare_profiles(self, payload: Dict[str, Any]) -> None:
        try:
            result = build_profile_comparison(payload)
        except UnknownProfileError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except ParseError as exc:
            self._send_json(422, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, result)

    def _post_generate(
        self,
        payload: Dict[str, Any],
        workspace: WorkspaceConfig,
        profile: Optional[str] = None,
        template: Optional[Any] = None,
    ) -> None:
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            self._send_json(400, {"error": "field 'content' is required"})
            return
        fmt = payload.get("format", "auto")
        title = payload.get("title", "Release Notes")
        if not isinstance(fmt, str) or not isinstance(title, str):
            self._send_json(400, {"error": "'format' and 'title' must be strings"})
            return
        try:
            result = build_draft(
                content, fmt, title, workspace, profile, template
            )
        except ParseError as exc:
            self._send_json(422, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": "invalid config: {}".format(exc)})
            return
        self._store(result)
        self._send_json(200, result)

    def _post_diff(
        self,
        payload: Dict[str, Any],
        workspace: WorkspaceConfig,
        profile: Optional[str] = None,
        template: Optional[Any] = None,
    ) -> None:
        previous = payload.get("previous")
        if not isinstance(previous, str) or not previous.strip():
            self._send_json(400, {"error": "field 'previous' is required"})
            return
        current = payload.get("current")
        if not isinstance(current, str) or not current.strip():
            self._send_json(400, {"error": "field 'current' is required"})
            return
        fmt = payload.get("format", "auto")
        title = payload.get("title", "Release Notes")
        if not isinstance(fmt, str) or not isinstance(title, str):
            self._send_json(400, {"error": "'format' and 'title' must be strings"})
            return
        try:
            result = build_diff(
                previous, current, fmt, title, workspace, profile, template
            )
        except ParseError as exc:
            self._send_json(422, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": "invalid config: {}".format(exc)})
            return
        self._store(result)
        self._send_json(200, result)


def create_server(
    host: str = "0.0.0.0", port: int = 8080
) -> Tuple[ThreadingHTTPServer, str, int]:
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    data_dir = os.environ.get("DATA_DIR")
    server.store = DraftStore(data_dir) if data_dir else None
    server.review_store = ReviewStore(data_dir, server.store)
    return server, server.server_address[0], server.server_address[1]


def main(argv: Optional[list] = None) -> int:
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("PORT", "8080"))
    except ValueError:
        port = 8080
    server, bound_host, bound_port = create_server(host, port)
    sys.stderr.write(
        "release-note-qa-copilot listening on http://{}:{}\n".format(
            bound_host, bound_port
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
