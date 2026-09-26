"""Response-size regression tests: a local server must not be able to grow
this process's memory without bound.

The default 32 MiB cap is config plumbing; these tests prove enforcement with
small limits against a real loopback HTTP server (never a wildcard bind),
covering the non-streaming body, the SSE event buffer, and the documented
disable switch.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from summarizer.config import EngineSettings
from summarizer.engine.client import HttpClient, MalformedResponse
from summarizer.engine.ollama import OllamaEngine


class _Handler(BaseHTTPRequestHandler):
    mode = "body"  # class attribute; subclasses override

    def do_POST(self):  # noqa: N802 - http.server API
        # Drain the request body before responding. An unread body left in the
        # receive queue makes close() send RST, which can race a client still
        # reading a large response (observed on GitHub's Python 3.11 runner).
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.mode == "sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            # One valid event first, then one deliberately unterminated event:
            # no blank line ever arrives, so an unbounded client would buffer
            # forever until the cap fires. Close-delimited on purpose.
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
            for _ in range(64):
                self.wfile.write(b"data: x" + b"y" * 65536 + b"\n")  # no blank line
                self.wfile.flush()
        else:
            body_size = 512 * 65536  # ~32 MiB
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            # Declare the length so the client stops at the last byte instead
            # of waiting for a close-delimited EOF.
            self.send_header("Content-Length", str(body_size))
            self.end_headers()
            for _ in range(512):
                self.wfile.write(b"z" * 65536)  # no framing games
                self.wfile.flush()

    def log_message(self, *_):
        pass


def _make_server(mode):
    class Bound(_Handler):
        pass

    Bound.mode = mode
    server = ThreadingHTTPServer(("127.0.0.1", 0), Bound)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _client(port: int, cap: int) -> HttpClient:
    return HttpClient(
        f"http://127.0.0.1:{port}",
        timeout=15.0,
        max_response_bytes=cap,
    )


@pytest.mark.parametrize("mode", ["body"])
def test_oversized_body_fails_cleanly(mode):
    server, thread = _make_server("body")
    try:
        client = _client(server.server_port, cap=1024 * 1024)  # 1 MiB cap
        with pytest.raises(MalformedResponse, match="exceeded"):
            client.post_json("/v1/chat/completions", {"model": "m", "messages": []})
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("mode", ["body"])
def test_body_within_cap_succeeds(mode):
    server, thread = _make_server("body")
    try:
        client = _client(server.server_port, cap=64 * 1024 * 1024)  # generous
        # The handler returns non-JSON bytes; MalformedResponse from *parsing*
        # is fine — the point is the cap did not fire early.
        with pytest.raises(MalformedResponse) as excinfo:
            client.post_json("/v1/chat/completions", {"model": "m", "messages": []})
        assert "exceeded" not in str(excinfo.value)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_sse_unterminated_event_capped():
    server, thread = _make_server("sse")
    try:
        # A 1 MiB cap (the buffer floor) trips after ~1 MiB of undelimited
        # data, long before the server finishes its 4 MiB of writes, so the
        # test is not sensitive to transfer speed under load.
        client = _client(server.server_port, cap=1024 * 1024)
        events = client.stream_sse("/v1/chat/completions", {"model": "m", "stream": True})
        first = next(iter(events))
        assert isinstance(first, dict)  # the well-formed event arrives
        with pytest.raises(MalformedResponse, match="without a delimiter"):
            for _ in events:
                pass
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_zero_cap_disables_the_limit():
    server, thread = _make_server("body")
    try:
        client = _client(server.server_port, cap=0)
        with pytest.raises(MalformedResponse) as excinfo:
            client.post_json("/v1/chat/completions", {"model": "m", "messages": []})
        assert "exceeded" not in str(excinfo.value)  # failure is JSON parsing, not the cap
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_engine_settings_default_cap_is_plumbed():
    engine = OllamaEngine(EngineSettings())
    assert engine._http.max_response_bytes == 32 * 1024 * 1024