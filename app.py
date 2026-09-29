"""Minimal Catalog Service skeleton without business logic."""

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class RequestHandler(BaseHTTPRequestHandler):
    """Serve only technical endpoints required to check the service."""

    def do_GET(self) -> None:  # noqa: N802 - method name is defined by BaseHTTPRequestHandler
        if self.path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {"status": "ok", "service": "catalog-service"},
            )
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _send_json(self, status: HTTPStatus, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def create_server(host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    """Create the HTTP server separately so it can be tested."""
    return ThreadingHTTPServer((host, port), RequestHandler)


if __name__ == "__main__":
    server_port = int(os.getenv("PORT", "8080"))
    server = create_server(port=server_port)
    print(f"Catalog Service is running on port {server_port}")
    server.serve_forever()
