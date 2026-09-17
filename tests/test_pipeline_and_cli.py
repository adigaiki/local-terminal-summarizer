"""Core behaviour tests for the local CLI pipeline.

These tests use a recording in-process engine: they prove the plumbing and
trust boundary without needing a running model server or network access.
"""

from __future__ import annotations

from dataclasses import replace
from io import StringIO
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from summarizer.chunking import chunking_decision
from summarizer.chunking.splitter import split_document
from summarizer.cli import build_parser, main
from summarizer.config import ChunkingSettings, Config, DefaultsSettings, EngineSettings, load_config
from summarizer.doctor import diagnose
from summarizer.engine import EngineCapabilities
from summarizer.engine.client import HttpClient, HttpStatusError
from summarizer.engine.ollama import OllamaEngine
from summarizer.engine.openai import OpenAICompatEngine
from summarizer.errors import InputError, ModelNotFound
from summarizer.log import Diagnostics
from summarizer.output.atomic import atomic_write
from summarizer.pipeline import Pipeline, PipelineOptions
from summarizer.profiles import load_profile


class RecordingEngine:
    backend = "fake-local"
    endpoint = "http://127.0.0.1:9999"
    model = "test-model"
    timeout_seconds = 1.0
    retries = 0

    def __init__(self, *, context_length: int = 5_000) -> None:
        self.prompts: list[str] = []
        self._caps = EngineCapabilities(context_length=context_length)

    def capabilities(self) -> EngineCapabilities:
        return self._caps

    def generate(self, *, prompt: str, json_object: bool = False, temperature=None) -> str:
        self.prompts.append(prompt)
        if json_object:
            return '{"summary": "structured"}'
        return f"summary {len(self.prompts)}"

    def stream(self, *, prompt: str, temperature=None):
        self.prompts.append(prompt)
        yield "streamed"

    def health(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return [self.model]


def config(*, max_tokens: int = 3_000, reserve: int = 100) -> Config:
    return Config(
        engine=EngineSettings(context_length=5_000),
        defaults=DefaultsSettings(output_format="plain", stream=False),
        chunking=ChunkingSettings(
            max_tokens_per_chunk=max_tokens,
            overlap_tokens=0,
            reserve_output_tokens=reserve,
        ),
    )


def test_pipeline_direct_json_envelope_includes_document_and_boundary(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("Useful local-only note.", encoding="utf-8")
    engine = RecordingEngine()
    pipeline = Pipeline(config(), diag=Diagnostics(quiet=True), engine=engine)

    result = pipeline.run(str(source), opts=PipelineOptions(output_format="json", stream=False))

    assert result.summary == {"summary": "structured"}
    assert result.boundary and result.boundary.startswith("document-")
    assert '"prompt_boundary"' in result.text
    assert "Useful local-only note." in engine.prompts[0]
    assert "SOURCE DOCUMENT (UNTRUSTED DATA)" in engine.prompts[0]


def test_map_reduce_uses_dedicated_reduce_boundary(tmp_path):
    source = tmp_path / "large.txt"
    source.write_text("one two three four five six seven eight nine ten " * 10, encoding="utf-8")
    engine = RecordingEngine()
    pipeline = Pipeline(config(max_tokens=20), diag=Diagnostics(quiet=True), engine=engine)

    result = pipeline.run(str(source), opts=PipelineOptions(stream=False))

    assert result.aggregation.name == "mapreduce"
    assert len(engine.prompts) > 2  # map calls followed by the reduce call
    assert result.boundary and result.boundary.startswith("interim-")
    assert "INTERIM SUMMARIES (UNTRUSTED DATA)" in engine.prompts[-1]
    assert "<interim-" in engine.prompts[-1]


def test_configured_default_chunk_strategy_is_used(tmp_path):
    source = tmp_path / "chars.txt"
    source.write_text("x" * 100, encoding="utf-8")
    custom = replace(config(max_tokens=10), defaults=DefaultsSettings(
        output_format="plain", stream=False, chunk_strategy="chars",
    ))
    pipeline = Pipeline(custom, diag=Diagnostics(quiet=True), engine=RecordingEngine())

    result = pipeline.run(str(source), opts=PipelineOptions(stream=False))

    assert result.decision.unit == "chars"


def test_context_is_labelled_and_never_mixed_with_document(tmp_path):
    source = tmp_path / "source.txt"
    context = tmp_path / "context.txt"
    source.write_text("source-only-data", encoding="utf-8")
    context.write_text("audience-only-context", encoding="utf-8")
    engine = RecordingEngine()

    Pipeline(config(), diag=Diagnostics(quiet=True), engine=engine).run(
        str(source), opts=PipelineOptions(stream=False, context_file=str(context)),
    )

    prompt = engine.prompts[0]
    assert "USER-SUPPLIED CONTEXT (TRUSTED)" in prompt
    assert prompt.index("audience-only-context") < prompt.index("SOURCE DOCUMENT (UNTRUSTED DATA)")
    document_section = prompt.split("SOURCE DOCUMENT (UNTRUSTED DATA)", 1)[1]
    assert "audience-only-context" not in document_section


def test_too_small_context_fails_with_actionable_input_error():
    with pytest.raises(InputError, match="context window is smaller"):
        chunking_decision(
            "content",
            capabilities=EngineCapabilities(context_length=100),
            settings=ChunkingSettings(reserve_output_tokens=100),
            profile=load_profile("plain"),
            config_context_length=0,
        )


def test_chunks_with_overlap_still_respect_the_requested_budget():
    chunks = split_document(
        "one two three four five six seven eight nine ten " * 3,
        max_units=12,
        overlap_units=3,
    )

    assert len(chunks) > 1
    assert all(chunk.token_estimate <= 12 for chunk in chunks)
    assert all(chunk.overlap_chars >= 0 for chunk in chunks)


def test_atomic_write_keeps_existing_file_when_body_fails(tmp_path):
    target = tmp_path / "summary.txt"
    target.write_text("old", encoding="utf-8")

    with pytest.raises(RuntimeError):
        with atomic_write(target) as handle:
            handle.write("new")
            raise RuntimeError("engine failed")

    assert target.read_text(encoding="utf-8") == "old"


class StatusHttp:
    base_path = ""

    def post_json(self, path, payload):
        raise HttpStatusError(404, '{"error":"model not found"}', path=path)


def test_openai_status_errors_map_to_engine_errors():
    engine = OpenAICompatEngine(EngineSettings(), http=StatusHttp())

    with pytest.raises(ModelNotFound):
        engine.generate(prompt="hello")


class CapturingHttp:
    base_path = ""

    def __init__(self) -> None:
        self.post_calls: list[tuple[str, dict]] = []
        self.stream_calls: list[tuple[str, dict]] = []

    def post_json(self, path, payload):
        self.post_calls.append((path, payload))
        return {
            "choices": [{"message": {
                "content": "final answer",
                "reasoning": "private reasoning must not be emitted",
            }}]
        }

    def stream_sse(self, path, payload):
        self.stream_calls.append((path, payload))
        yield {"choices": [{"delta": {"reasoning": "private"}}]}
        yield {"choices": [{"delta": {"content": "final"}}]}


def test_ollama_payload_bounds_output_and_disables_reasoning_by_default():
    http = CapturingHttp()
    engine = OllamaEngine(EngineSettings(max_tokens=321, reasoning_effort="none"), http=http)

    assert engine.generate(prompt="trusted prompt") == "final answer"
    path, payload = http.post_calls[0]
    assert path == "/v1/chat/completions"
    assert payload == {
        "model": "llama3.1:8b",
        "messages": [{"role": "user", "content": "trusted prompt"}],
        "stream": False,
        "max_tokens": 321,
        "reasoning_effort": "none",
        "temperature": 0.3,
    }

    assert "".join(engine.stream(prompt="stream prompt")) == "final"
    _, stream_payload = http.stream_calls[0]
    assert stream_payload["stream"] is True
    assert stream_payload["max_tokens"] == 321
    assert stream_payload["reasoning_effort"] == "none"


def test_generic_openai_payload_omits_unknown_reasoning_control():
    http = CapturingHttp()
    engine = OpenAICompatEngine(EngineSettings(max_tokens=88, reasoning_effort="low"), http=http)

    engine.generate(prompt="prompt")

    payload = http.post_calls[0][1]
    assert payload["max_tokens"] == 88
    assert "reasoning_effort" not in payload


def test_engine_config_parses_reasoning_and_output_limits(tmp_path):
    project = tmp_path / "summarizer.toml"
    project.write_text(
        "[engine]\nmax_tokens = 456\nreasoning_effort = 'low'\n",
        encoding="utf-8",
    )

    loaded = load_config(user_path=tmp_path / "absent.toml", project_path=project)

    assert loaded.engine.max_tokens == 456
    assert loaded.engine.reasoning_effort == "low"


def test_http_client_replaces_connect_timeout_with_request_deadline():
    class Socket:
        def __init__(self):
            self.timeouts: list[float] = []

        def settimeout(self, value):
            self.timeouts.append(value)

    class Connection:
        def __init__(self):
            self.sock = Socket()

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return object()

    conn = Connection()
    HttpClient._send(conn, "POST", "/v1/chat/completions", {"x": 1}, timeout=180)

    assert conn.sock.timeouts[-1] > 170


def test_doctor_reports_a_reachable_but_missing_configured_model():
    engine = RecordingEngine()
    engine.model = "llama3.1:8b"
    engine.list_models = lambda: ["qwen3:8b"]
    report = diagnose(config(), diag=Diagnostics(quiet=True), engine=engine)

    model_check = next(check for check in report.checks if check.name == "Model available")
    assert not model_check.ok
    assert "not installed" in model_check.message
    assert "qwen3:8b" in model_check.hint


def test_cli_parser_keeps_normal_file_inputs_distinct_from_commands():
    parser = build_parser()
    assert parser.parse_args(["README.md"]).operands == ["README.md"]
    assert main(["README.md", "--timeout", "0", "--quiet"]) == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["one.txt", "two.txt"],
        ["--definitely-not-a-flag", "one.txt"],
        ["session", "wat"],
        ["doctor", "extra.txt"],
    ],
)
def test_cli_usage_errors_use_the_documented_input_exit_code(argv, capsys):
    """Usage errors exit 1; exit 2 is reserved for local engine errors.

    argparse's default is 2, which would collide with the documented
    "local engine error" status in README "Exit status".
    """
    with pytest.raises(SystemExit) as excinfo:
        main(argv)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage: summarize" in captured.err
    assert "error:" in captured.err


def test_cli_runs_end_to_end_against_a_local_openai_compatible_server(tmp_path, capsys):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            size = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(size))
            assert request["model"] == "mock-model"
            assert request["stream"] is False
            assert request["max_tokens"] == 1024
            assert "reasoning_effort" not in request  # generic compatibility mode
            assert request["messages"] and request["messages"][0]["role"] == "user"
            assert "SOURCE DOCUMENT (UNTRUSTED DATA)" in request["messages"][0]["content"]
            body = json.dumps({"choices": [{"message": {"content": "local summary"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except PermissionError:
        pytest.skip("the current sandbox does not permit binding a local test server")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source = tmp_path / "input.txt"
    source.write_text("A document for the local test server.", encoding="utf-8")
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        code = main([
            str(source), "--backend", "openai", "--endpoint", endpoint,
            "--model", "mock-model", "--no-stream", "--quiet",
        ])
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "local summary\n"
    assert captured.err == ""
