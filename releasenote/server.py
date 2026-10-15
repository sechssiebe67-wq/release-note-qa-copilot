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
from .importer import ParseError
from .service import build_draft, sample, sample_workspace_config
from .storage import DraftStore

MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MiB upload cap.
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


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
            raise ParseError("Request body exceeds 2 MiB limit")
        return self.rfile.read(length)

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
        self._send_json(404, {"error": "not found", "path": parsed.path})

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path != "/api/generate":
            self._send_json(404, {"error": "not found", "path": parsed.path})
            return
        try:
            raw = self._read_body()
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "body must be valid JSON"})
            return
        except ParseError as exc:
            self._send_json(413, {"error": str(exc)})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "body must be a JSON object"})
            return
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            self._send_json(400, {"error": "field 'content' is required"})
            return
        fmt = payload.get("format", "auto")
        title = payload.get("title", "Release Notes")
        if not isinstance(fmt, str) or not isinstance(title, str):
            self._send_json(400, {"error": "'format' and 'title' must be strings"})
            return
        config_payload = payload.get("config")
        if config_payload is not None and not isinstance(config_payload, dict):
            self._send_json(400, {"error": "'config' must be a JSON object"})
            return
        try:
            workspace = WorkspaceConfig.from_dict(config_payload)
        except ValueError as exc:
            self._send_json(400, {"error": "invalid config: {}".format(exc)})
            return
        try:
            result = build_draft(content, fmt, title, workspace)
        except ParseError as exc:
            self._send_json(422, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": "invalid config: {}".format(exc)})
            return
        store = getattr(self.server, "store", None)
        if store is not None:
            record = store.save(
                result["title"],
                result["markdown"],
                result["entry_count"],
                result["qa"]["counts"]["high"],
            )
            result["draft_id"] = record["id"]
        self._send_json(200, result)


def create_server(
    host: str = "0.0.0.0", port: int = 8080
) -> Tuple[ThreadingHTTPServer, str, int]:
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    data_dir = os.environ.get("DATA_DIR")
    server.store = DraftStore(data_dir) if data_dir else None
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
