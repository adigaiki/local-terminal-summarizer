"""Output behavior: broken pipes, statistics, atomic file failures."""

from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace

import pytest

from summarizer import cli
from summarizer.cli import _print_stats, _quiet_broken_pipe, main
from summarizer.config import ChunkingSettings, Config, DefaultsSettings, EngineSettings
from summarizer.engine import EngineCapabilities
from summarizer.errors import InputError
from summarizer.log import Diagnostics
from summarizer.output.atomic import atomic_write
from summarizer.pipeline import Pipeline, PipelineOptions


class TinyEngine:
    backend = "fake-local"
    endpoint = "http://127.0.0.1:9"
    model = "fake"
    timeout_seconds = 1.0
    retries = 0
    max_tokens = 1024

    def capabilities(self):
        return EngineCapabilities(context_length=5000)

    def generate(self, *, prompt, json_object=False, temperature=None):
        return '{"summary": "structured"}' if json_object else "a summary"

    def stream(self, *, prompt, temperature=None):
        yield "a summary"

    def health(self):
        return True

    def list_models(self):
        return [self.model]


# --- broken pipe ------------------------------------------------------------


def test_broken_pipe_returns_141_without_traceback(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise BrokenPipeError()

    monkeypatch.setattr(cli, "_run_summarize", boom)
    monkeypatch.setattr(cli, "_quiet_broken_pipe", lambda: None)

    code = main(["--quiet"])

    captured = capsys.readouterr()
    assert code == 141
    assert "Traceback" not in captured.err


def test_quiet_broken_pipe_tolerates_an_unusable_stdout(monkeypatch):
    class NoFileno:
        def fileno(self):
            raise OSError("no fileno")

        def close(self):
            pass

    monkeypatch.setattr(sys, "stdout", NoFileno())
    _quiet_broken_pipe()  # must not raise


# --- statistics -------------------------------------------------------------


def test_print_stats_renders_observations_not_content():
    result = SimpleNamespace(
        stats={
            "input_bytes": 1234,
            "input_chars": 1230,
            "input_tokens_est": 300,
            "chunks": 4,
            "concurrency": 2,
            "cached_chunks": 1,
            "map_seconds": 2.5,
            "reduce_seconds": 1.0,
            "generation_seconds": 3.5,
            "total_seconds": 4.0,
            "generated_tokens_est": 350,
            "tokens_per_second_est": 100.0,
        },
        engine=SimpleNamespace(last_usage={"completion_tokens": 42}),
    )
    stream = io.StringIO()
    _print_stats(result, stream=stream)
    text = stream.getvalue()
    assert "chunks: 4" in text
    assert "map: 2.500s" in text
    assert "generated: ~350 tokens" in text
    assert "throughput" in text
    assert "backend-reported output tokens (last request): 42" in text
    assert "a summary" not in text  # no document/model content leaks into stats


def test_json_envelope_carries_stats_only_when_requested(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("A short local note.", encoding="utf-8")
    config = Config(
        engine=EngineSettings(context_length=5000),
        defaults=DefaultsSettings(output_format="json", stream=False),
        chunking=ChunkingSettings(max_tokens_per_chunk=3000, overlap_tokens=0, reserve_output_tokens=100),
    )
    without = Pipeline(config, diag=Diagnostics(quiet=True), engine=TinyEngine()).run(
        str(source), opts=PipelineOptions(output_format="json", stream=False)
    )
    assert "stats" not in json.loads(without.text)

    with_stats = Pipeline(config, diag=Diagnostics(quiet=True), engine=TinyEngine()).run(
        str(source), opts=PipelineOptions(output_format="json", stream=False, stats=True)
    )
    payload = json.loads(with_stats.text)
    assert payload["stats"]["chunks"] == 1
    assert payload["stats"]["input_bytes"] > 0


# --- output file failures ---------------------------------------------------


def test_atomic_write_refuses_a_directory_target(tmp_path):
    directory = tmp_path / "out"
    directory.mkdir()
    with pytest.raises(InputError, match="directory"):
        with atomic_write(directory):
            pass


def test_atomic_write_refuses_a_missing_parent(tmp_path):
    with pytest.raises(InputError, match="does not exist"):
        with atomic_write(tmp_path / "missing" / "out.txt"):
            pass


def test_streaming_writes_are_flushed_to_a_non_tty_stream():
    from summarizer.output.stream import stream_to_io

    buffer = io.StringIO()
    stream_to_io(iter(["a", "b", "c"]), buffer)
    assert buffer.getvalue() == "abc"


# --- CLI end to end against a loopback server -------------------------------


def _serve(handler_cls):
    import threading
    from http.server import ThreadingHTTPServer

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    except PermissionError:
        pytest.skip("the current sandbox does not permit binding a local test server")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class _SummaryHandler:
    pass


def _make_handler():
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            request = json.loads(self.rfile.read(length) or b"{}")
            content = (
                '{"summary": "local summary"}'
                if request.get("response_format")
                else "local summary"
            )
            body = json.dumps(
                {"choices": [{"message": {"content": content}}],
                 "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    return Handler


def test_cli_stats_go_to_stderr_and_json_stays_valid(tmp_path, capsys):
    server, thread = _serve(_make_handler())
    source = tmp_path / "input.txt"
    source.write_text("A document for the local test server.", encoding="utf-8")
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        code = main([
            str(source), "--backend", "openai", "--endpoint", endpoint,
            "--model", "mock", "--format", "json", "--stats", "--quiet",
        ])
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)  # stdout is still valid JSON
    assert payload["stats"]["chunks"] == 1
    assert "Stats:" in captured.err  # explicitly requested, so shown even with --quiet
    assert "Summarizing" not in captured.err  # progress itself is silenced


def test_cli_progress_is_plain_on_a_non_tty_and_never_on_stdout(tmp_path, capsys):
    server, thread = _serve(_make_handler())
    source = tmp_path / "input.txt"
    source.write_text("A document for the local test server.", encoding="utf-8")
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        code = main([
            str(source), "--backend", "openai", "--endpoint", endpoint,
            "--model", "mock", "--no-stream", "--stats",
        ])
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "local summary\n"  # stdout carries output only
    assert "Stats:" in captured.err
    assert "Summarizing" in captured.err
    assert "\r" not in captured.err  # non-TTY: no interactive bar


def test_cli_concurrency_flag_is_accepted(tmp_path, capsys):
    server, thread = _serve(_make_handler())
    source = tmp_path / "input.txt"
    source.write_text("A document for the local test server.", encoding="utf-8")
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        code = main([
            str(source), "--backend", "openai", "--endpoint", endpoint,
            "--model", "mock", "--no-stream", "--quiet", "--concurrency", "2",
        ])
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    assert code == 0
    assert capsys.readouterr().out == "local summary\n"


def test_cli_rejects_out_of_range_concurrency(capsys):
    code = main(["--concurrency", "99", "--quiet"])
    assert code == 1
    assert "concurrency" in capsys.readouterr().err

