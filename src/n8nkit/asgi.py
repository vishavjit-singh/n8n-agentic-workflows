"""Call an ASGI app in-process, without a socket or httpx (the simulator and tests use this for the mock API)."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self):
        return json.loads(self.body.decode() or "null")


def request(app, method: str, url: str, headers: dict[str, str] | None = None, body: bytes | dict | None = None
            ) -> Response:
    parts = urlsplit(url)
    if isinstance(body, dict):
        body = json.dumps(body).encode()
        headers = {"content-type": "application/json", **(headers or {})}
    body = body or b""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method.upper(),
        "scheme": parts.scheme or "http", "path": parts.path or "/", "raw_path": (parts.path or "/").encode(),
        "query_string": parts.query.encode(), "root_path": "",
        "headers": [(k.lower().encode(), str(v).encode()) for k, v in (headers or {}).items()],
        "client": ("127.0.0.1", 0), "server": (parts.hostname or "testserver", parts.port or 80),
    }
    sent = {"status": 500, "headers": {}, "body": b""}
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
            sent["headers"] = {k.decode(): v.decode() for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    asyncio.run(app(scope, receive, send))
    return Response(sent["status"], sent["headers"], sent["body"])
