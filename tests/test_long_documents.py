"""v0.2 regressions. No model server or external network required."""
from dataclasses import replace
from io import BytesIO
import json
import re

import pytest

from summarizer.aggregation.mapreduce import MapReduce, plan_reduce_groups
from summarizer.chunking import compute_input_budget, chunking_decision, estimate_tokens
from summarizer.chunking.splitter import split_document
from summarizer.config import ChunkingSettings, EngineSettings
from summarizer.engine.ollama import OllamaEngine
from summarizer.errors import InputError, MalformedResponse
from summarizer.log import Diagnostics
from summarizer.output.json import generate_json, parse_json_strict
from summarizer.pipeline import Pipeline, PipelineOptions
from summarizer.profiles import load_profile
from summarizer.prompt import PromptBuilder
from summarizer.readers.stdin import bounded_read, read_stdin
from test_pipeline_and_cli import RecordingEngine, config


@pytest.mark.parametrize('metadata,expected', [
    ({'model_info': {'some_arch.context_length': 16384}}, 16384),
    ({'parameters': {'num_ctx': 4096}}, 4096),
    ({}, None),
    ({'model_info': {'some_arch.context_length': -1}}, None),
])
def test_context_metadata(metadata, expected):
    assert OllamaEngine._context_from_show(metadata)[0] == expected


def test_capability_discovery_is_cached():
    class HTTP:
        calls = 0
        def post_json(self, path, payload, **kwargs):
            assert path == '/api/show'
            self.calls += 1
            return {'model_info': {'architecture.context_length': 12345}}
    http = HTTP()
    engine = OllamaEngine(EngineSettings(), http=http)
    assert engine.capabilities().context_length == 12345
    assert engine.capabilities().context_source == 'server'
    assert http.calls == 1


def test_pure_budget_subtracts_all_reservations():
    budget = compute_input_budget(context_length=4096, prompt_tokens=400,
        context_file_tokens=200, reserved_output_tokens=512, max_tokens_per_chunk=3000)
    assert budget.usable_input_tokens == 2984
    assert budget.claimed_tokens == 4096
    assert budget.max_chunk_tokens == 2984


@pytest.mark.parametrize('unit', ['chars', 'tokens'])
def test_chunk_provenance_and_overlap(unit):
    text = 'First paragraph has several words.\n\nSecond paragraph is useful.\n\n' * 10
    chunks = split_document(text, max_units=80, overlap_units=10, unit=unit, source='fixture.md')
    assert len(chunks) > 1
    assert any(c.overlap_chars for c in chunks[1:])
    for c in chunks:
        assert (len(c.text) if unit == 'chars' else estimate_tokens(c.text)) <= 80
        assert c.provenance(total=len(chunks))['source'] == 'fixture.md'
        assert c.text[c.overlap_chars:] == text[c.start_char:c.end_char]


def test_paragraph_separators_are_not_lost():
    text = 'alpha\n\nbeta\n\ngamma'
    chunks = split_document(text, max_units=10, overlap_units=0, unit='chars')
    assert ''.join(c.text for c in chunks) == text


def test_sentence_boundaries():
    chunks = split_document('First sentence. Second sentence. Third sentence.',
        max_units=20, overlap_units=0, unit='chars')
    assert chunks[0].text.rstrip().endswith('.')
    assert chunks[0].boundary == 'sentence'


def test_strict_refuses_before_generation(tmp_path):
    path = tmp_path / 'large.txt'
    path.write_text('word ' * 100)
    engine = RecordingEngine()
    with pytest.raises(InputError, match='strict'):
        Pipeline(config(max_tokens=20), diag=Diagnostics(quiet=True), engine=engine).run(
            str(path), opts=PipelineOptions(strict=True))
    assert engine.prompts == []


def test_actual_chunk_limit_enforced(tmp_path):
    path = tmp_path / 'large.txt'
    path.write_text(('word ' * 8 + '\n\n') * 10)
    cfg = replace(config(max_tokens=20), chunking=ChunkingSettings(
        max_tokens_per_chunk=20, overlap_tokens=18, reserve_output_tokens=100, max_chunks=2))
    engine = RecordingEngine()
    with pytest.raises(InputError, match='maximum|limit|chunks'):
        Pipeline(cfg, diag=Diagnostics(quiet=True), engine=engine).run(str(path), opts=PipelineOptions())
    assert not engine.prompts


def test_json_recovery_once_without_replaying_untrusted_output():
    calls = []
    def request(prompt, **kwargs):
        calls.append(prompt)
        return 'MALICIOUS-REPLY' if len(calls) == 1 else '{"summary":"ok"}'
    assert generate_json(request, 'trusted prompt', diag=Diagnostics(quiet=True),
        structured_supported=True) == {'summary': 'ok'}
    assert len(calls) == 2
    assert 'MALICIOUS-REPLY' not in calls[1]


def test_json_failure_after_two_attempts():
    calls = []
    def request(prompt, **kwargs):
        calls.append(prompt)
        return 'not json'
    with pytest.raises(MalformedResponse):
        generate_json(request, 'prompt', diag=Diagnostics(quiet=True), structured_supported=False)
    assert len(calls) == 2


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '{"x":NaN}', '{"x":1e999}'])
def test_json_nonfinite_rejected(value):
    assert not parse_json_strict(value)[0]


def test_infinite_stdin_is_bounded():
    class Infinite:
        calls = 0
        def read(self, size):
            assert size > 0
            self.calls += 1
            return b'x' * size
    stream = Infinite()
    with pytest.raises(InputError, match='bytes'):
        bounded_read(stream, max_bytes=1024, max_lines=100)
    assert stream.calls == 1


def test_line_limit_includes_unterminated_line():
    with pytest.raises(InputError, match='lines'):
        bounded_read(BytesIO(b'one\ntwo'), max_bytes=100, max_lines=1)


def test_stdin_provenance():
    doc = read_stdin(raw=BytesIO(b'hello'), diag=Diagnostics(quiet=True))
    assert doc.source == 'stdin'
    assert doc.encoding == 'utf-8'
    assert doc.size == 5
    assert doc.mime_type == 'text/plain'
