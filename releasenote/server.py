"""Minimal HTTP server for the Release Note QA Copilot.

Only the Python standard library is used so the container stays small and
dependency free. The server exposes a small JSON API plus a browser UI.
"""

from __future__ import annotations

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
    diff_csv,
    draft_json,
    qa_flags_csv,
    rules_csv,
    rules_json,
)
from .importer import ParseError
from .profiles import (
    UnknownProfileError,
    list_profiles,
    normalize_profile_name,
    resolve_config,
)
from .service import (
    build_diff,
    build_draft,
    get_review,
    record_review,
    sample,
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
    "/api/export/draft.json": ("application/json; charset=utf-8", "release-draft.json"),
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
        if path in _EXPORTS:
            self._handle_export(path, parsed)
            return
        self._send_json(404, {"error": "not found", "path": parsed.path})

    def _handle_export(self, path: str, parsed: Any) -> None:
        """Serve a CSV or JSON export built from query parameters.

        The endpoints mirror ``/api/generate`` and ``/api/diff``: ``format``,
        ``title`` and ``content`` (plus ``previous``/``current`` for the diff
        export) are accepted as query parameters. Missing or invalid
        parameters return 400, while unparseable content returns 422.
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
                    previous, current, fmt, title, workspace, profile_name
                )
                body = diff_csv(result)
            else:
                content = _first(query, "content")
                if not content or not content.strip():
                    self._send_json(400, {"error": "query 'content' is required"})
                    return
                result = build_draft(
                    content, fmt, title, workspace, profile_name
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
    ) -> Optional[Tuple[WorkspaceConfig, Optional[str]]]:
        """Resolve the optional ``profile``/``config`` fields.

        Returns ``(workspace, profile_name)`` where ``profile_name`` is the
        resolved profile or ``None``. On error a 400 response is sent and
        ``None`` is returned; an unknown profile names the profile in the
        message.
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
        return workspace, profile_name

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path not in ("/api/generate", "/api/diff", "/api/review"):
            self._send_json(404, {"error": "not found", "path": parsed.path})
            return
        payload = self._parse_body()
        if payload is None:
            return
        if path == "/api/review":
            self._post_review(payload)
            return
        resolved = self._resolve_request(payload)
        if resolved is None:
            return
        workspace, profile_name = resolved
        if path == "/api/generate":
            self._post_generate(payload, workspace, profile_name)
        else:
            self._post_diff(payload, workspace, profile_name)

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

    def _post_generate(
        self,
        payload: Dict[str, Any],
        workspace: WorkspaceConfig,
        profile: Optional[str] = None,
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
            result = build_draft(content, fmt, title, workspace, profile)
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
            result = build_diff(previous, current, fmt, title, workspace, profile)
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
