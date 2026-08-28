"""Minimal test-only Core HTTP server for the delivery integration stack."""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

RECEIVED: list[dict[str, Any]] = []
RECEIVED_LOCK = threading.Lock()


class CoreHandler(BaseHTTPRequestHandler):
    server_version = "SeniorPomidorMockCore/1.0"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == "/received":
            with RECEIVED_LOCK:
                payloads = list(RECEIVED)
            self._write_json(HTTPStatus.OK, {"payloads": payloads})
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"status": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/api/v1/edge/telemetry":
            self._write_json(HTTPStatus.NOT_FOUND, {"status": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._write_json(HTTPStatus.BAD_REQUEST, {"status": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self._write_json(HTTPStatus.BAD_REQUEST, {"status": "invalid_payload"})
            return

        # Persist the request before acknowledging it, matching the Core contract.
        with RECEIVED_LOCK:
            RECEIVED.append(payload)
        record_id = payload.get("record_id")
        self._write_json(
            HTTPStatus.ACCEPTED,
            {"status": "accepted", "record_id": record_id},
        )

    def log_message(self, format: str, *args: object) -> None:
        print(f"mock-core: {format % args}", flush=True)

    def _write_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8080), CoreHandler)
    print("mock-core: listening on port 8080", flush=True)
    server.serve_forever()
