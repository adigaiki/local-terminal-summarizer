"""Cancellation is first-class: cooperative token, pipeline stages, streaming.

No model server is required; in-flight transport cancellation is exercised
against a throwaway loopback HTTP server.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from summarizer.cancel import CancelToken
from summarizer.config import ChunkingSettings, Config, DefaultsSettings, EngineSettings
from summarizer.engine import EngineCapabilities
from summarizer.engine.client import HttpClient
from summarizer.errors import Interrupted
from summarizer.log import Diagnostics
from summarizer.output.atomic import atomic_write
from summarizer.pipeline import Pipeline, PipelineOptions


class CancelAfterFirstEngine:
    backend = "fake-local"
    endpoint = "http://127.0.0.1:9"
    model = "fake"
    timeout_seconds = 1.0
    retries = 0
    max_tokens = 1024

    def __init__(self, token: CancelToken) -> None:
        self.token = token
        self.calls = 0

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(context_length=5000, streaming=True)

    def generate(self, *, prompt: str, json_object: bool = False, temperature=None) -> str:
        self.calls += 1
        self.token.cancel()
        return "summary text"

    def stream(self, *, prompt: str, temperature=None):
        self.token.cancel()
        yield "partial"

    def health(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return [self.model]


class StreamingEngine:
    backend = "fake-local"
    endpoint = "http://127.0.0.1:9"
    model = "fake"
    timeout_seconds = 1.0
    retries = 0
    max_tokens = 1024

    def __init__(self, token: CancelToken, *, cancel_after: int = 2) -> None:
        self.token = token
        self.cancel_after = cancel_after
        self.emitted = 0

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(context_length=5000, streaming=True)

    def generate(self, *, prompt: str, json_object: bool = False, temperature=None) -> str:
        return "full"

    def stream(self, *, prompt: str, temperature=None):
        for i in range(10):
            self.emitted += 1
            if self.emitted == self.cancel_after:
                self.token.cancel()
            yield f"tok{i} "

    def health(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return [self.model]


def _config(**chunking):
    settings = ChunkingSettings(max_tokens_per_chunk=chunking.get("max_tokens_per_chunk", 3000),
                                overlap_tokens=0, reserve_output_tokens=10,
                                max_chunks=chunking.get("max_chunks", 64))
    return Config(
        engine=EngineSettings(context_length=5000),
        defaults=DefaultsSettings(output_format="plain", stream=False),
        chunking=settings,
    )


# --- token ------------------------------------------------------------------


def test_cancel_token_is_one_way_and_thread_safe():
    token = CancelToken()
    assert token.cancelled is False
    token.raise_if_cancelled()  # no-op
    token.cancel()
    assert token.cancelled is True
    with pytest.raises(Interrupted):
        token.raise_if_cancelled()
    token.cancel()  # idempotent


# --- pipeline stages --------------------------------------------------------


def test_pre_cancelled_run_never_calls_the_engine(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("A small local note.", encoding="utf-8")
    token = CancelToken(cancelled=True)
    engine = CancelAfterFirstEngine(token)
    pipeline = Pipeline(_config(), diag=Diagnostics(quiet=True), engine=engine, cancel=token)
    with pytest.raises(Interrupted):
        pipeline.run(str(source), opts=PipelineOptions(stream=False))
    assert engine.calls == 0


def test_cancellation_during_single_shot_stops_before_output(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("A small local note.", encoding="utf-8")
    token = CancelToken()
    engine = CancelAfterFirstEngine(token)
    pipeline = Pipeline(_config(), diag=Diagnostics(quiet=True), engine=engine, cancel=token)
    with pytest.raises(Interrupted):
        pipeline.run(str(source), opts=PipelineOptions(stream=False))
    assert engine.calls == 1


def test_cancellation_stops_remaining_map_chunks(tmp_path):
    source = tmp_path / "large.txt"
    source.write_text("word " * 4000, encoding="utf-8")
    token = CancelToken()
    engine = CancelAfterFirstEngine(token)
    pipeline = Pipeline(
        _config(max_tokens_per_chunk=100),
        diag=Diagnostics(quiet=True),
        engine=engine,
        cancel=token,
    )
    with pytest.raises(Interrupted):
        pipeline.run(str(source), opts=PipelineOptions(stream=False))
    # Exactly one map call happened; the rest were stopped.
    assert engine.calls == 1


def test_streaming_cancellation_keeps_partial_output_and_stops(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("A small local note.", encoding="utf-8")
    token = CancelToken()
    engine = StreamingEngine(token, cancel_after=2)
    emitted: list[str] = []
    pipeline = Pipeline(_config(), diag=Diagnostics(quiet=True), engine=engine, cancel=token)
    with pytest.raises(Interrupted):
        pipeline.run(
            str(source),
            opts=PipelineOptions(stream=True),
            emit=emitted.append,
        )
    assert "".join(emitted) == "tok0 "
    assert engine.emitted < 10  # stopped early


# --- transport-level cancellation ------------------------------------------


class SlowStreamHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server API
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n')
        self.wfile.flush()
        time.sleep(3.0)  # hold the stream open so cancellation must interrupt it

    def log_message(self, *_):
        pass


def test_in_flight_stream_can_be_cancelled_from_another_thread():
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowStreamHandler)
    except PermissionError:
        pytest.skip("the current sandbox does not permit binding a local test server")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = HttpClient(f"http://127.0.0.1:{server.server_port}", timeout=10.0)
        token = CancelToken()
        client.cancel = token
        events = client.stream_sse("/v1/chat/completions", {"model": "m", "stream": True})
        first = next(iter(events))
        assert isinstance(first, dict)
        timer = threading.Timer(0.2, token.cancel)
        timer.start()
        started = time.monotonic()
        with pytest.raises(Interrupted):
            for _ in events:
                pass
        timer.cancel()
        assert time.monotonic() - started < 2.5  # observed promptly
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


# --- output files are never corrupted ---------------------------------------


def test_atomic_write_preserves_target_on_interruption(tmp_path):
    target = tmp_path / "summary.txt"
    target.write_text("old content", encoding="utf-8")
    with pytest.raises(Interrupted):
        with atomic_write(target) as handle:
            handle.write("partial new content")
            raise Interrupted("interrupted")
    assert target.read_text(encoding="utf-8") == "old content"
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())
