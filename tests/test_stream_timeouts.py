"""Streaming deadlines: a slow first token is allowed; a stall is not.

Prompt processing on a CPU-bound model can take minutes before the first byte,
so the wait for the first byte is bounded by ``first_token_timeout`` while a
stall after the first byte is bounded by the idle ``timeout`` (reset on every
byte). A steady stream may run longer than ``timeout`` overall.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from summarizer.engine.client import HttpClient
from summarizer.errors import EngineTimeout

_EVENT = b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'


class _Handler(BaseHTTPRequestHandler):
    mode = "steady"

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if self.mode == "stall_before":
            time.sleep(3)
        elif self.mode == "stall_after":
            self.wfile.write(_EVENT)
            self.wfile.flush()
            time.sleep(3)
        else:  # steady: 6 events over ~1.8s, idle well under the 1.0s timeout
            for _ in range(6):
                self.wfile.write(_EVENT)
                self.wfile.flush()
                time.sleep(0.3)

    def log_message(self, *_):
        pass


def _server(mode: str):
    class Bound(_Handler):
        pass

    Bound.mode = mode
    server = ThreadingHTTPServer(("127.0.0.1", 0), Bound)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _client(server, **kwargs) -> HttpClient:
    return HttpClient(
        f"http://127.0.0.1:{server.server_port}",
        timeout=kwargs.pop("timeout", 1.0),
        **kwargs,
    )


def test_no_first_byte_times_out_at_the_first_token_deadline():
    server = _server("stall_before")
    try:
        client = _client(server, timeout=1.0, first_token_timeout=1.0)
        with pytest.raises(EngineTimeout, match="before the first token"):
            list(client.stream_sse("/v1/chat/completions", {"model": "m"}))
    finally:
        server.shutdown()


def test_stall_after_the_first_byte_times_out_at_the_idle_deadline():
    server = _server("stall_after")
    try:
        client = _client(server, timeout=1.0, first_token_timeout=5.0)
        events = client.stream_sse("/v1/chat/completions", {"model": "m"})
        assert next(iter(events))  # the first event arrives
        with pytest.raises(EngineTimeout, match="after the first token"):
            list(events)
    finally:
        server.shutdown()


def test_a_steady_stream_outlives_the_total_timeout():
    server = _server("steady")
    try:
        # Total run ~1.8s > timeout (1.0s), but each gap is 0.3s, so the idle
        # timeout never fires: the run must complete normally.
        client = _client(server, timeout=1.0, first_token_timeout=1.0)
        events = list(client.stream_sse("/v1/chat/completions", {"model": "m"}))
        assert len(events) >= 5
    finally:
        server.shutdown()
