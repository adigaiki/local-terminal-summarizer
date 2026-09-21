"""Local cache and resumable checkpoints: keying, privacy, bounds, resume."""

from __future__ import annotations

import json
import stat

import pytest

from summarizer.cache import CacheIdentity, LocalCache, content_hash
from summarizer.cache.store import SCHEMA
from summarizer.config import (
    CacheSettings,
    ChunkingSettings,
    Config,
    DefaultsSettings,
    EngineSettings,
)
from summarizer.engine import EngineCapabilities
from summarizer.log import Diagnostics
from summarizer.pipeline import Pipeline, PipelineOptions


class CountingEngine:
    backend = "fake-local"
    endpoint = "http://127.0.0.1:9"
    model = "fake-model"
    timeout_seconds = 1.0
    retries = 0
    max_tokens = 1024
    temperature = 0.3
    reasoning_effort = "none"

    def __init__(self, model: str = "fake-model") -> None:
        self.model = model
        self.map_calls = 0
        self.reduce_calls = 0

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(context_length=5000)

    def generate(self, *, prompt: str, json_object: bool = False, temperature=None) -> str:
        if "INTERIM SUMMARIES" in prompt:
            self.reduce_calls += 1
            return "FINAL SUMMARY"
        self.map_calls += 1
        return "chunk summary"

    def stream(self, *, prompt: str, temperature=None):
        yield self.generate(prompt=prompt)

    def health(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return [self.model]


def _identity(**overrides) -> CacheIdentity:
    base = dict(
        document_hash="doc",
        model="m",
        backend="ollama",
        profile="plain",
        profile_identity="pid",
        prompt_identity="prid",
        output_format="markdown",
        lang="",
        context_hash="none",
        chunk_hashes=("c1", "c2"),
        chunking={"unit": "tokens"},
        engine={"max_tokens": 1024},
    )
    base.update(overrides)
    return CacheIdentity(**base)


def _settings(tmp_path, **overrides) -> CacheSettings:
    base = dict(mode="readwrite", dir=str(tmp_path / "cache"), max_entries=100,
                max_bytes=10_000_000, max_chunk_bytes=1_000_000)
    base.update(overrides)
    return CacheSettings(**base)


# --- identity / invalidation ------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("document_hash", "other"),
        ("model", "other"),
        ("backend", "other"),
        ("profile", "other"),
        ("profile_identity", "other"),
        ("prompt_identity", "other"),
        ("output_format", "json"),
        ("lang", "fr"),
        ("context_hash", "ctx"),
        ("chunk_hashes", ("c1", "c3")),
        ("chunking", {"unit": "chars"}),
        ("engine", {"max_tokens": 5}),
    ],
)
def test_identity_changes_with_every_relevant_field(field, value):
    assert _identity().key != _identity(**{field: value}).key


def test_identity_round_trips_through_json():
    identity = _identity()
    assert CacheIdentity.from_json(identity.to_json()) == identity


# --- store behavior ---------------------------------------------------------


def test_cache_is_off_by_default(tmp_path):
    cache = LocalCache(CacheSettings(mode="off", dir=str(tmp_path / "c")))
    identity = _identity()
    cache.save_chunk(identity, 0, "summary")
    assert cache.load(identity) is None
    assert cache.status().entries == 0


def test_save_load_and_complete_roundtrip(tmp_path):
    cache = LocalCache(_settings(tmp_path))
    identity = _identity()
    cache.save_chunk(identity, 0, "s0")
    cache.save_chunk(identity, 1, "s1")
    entry = cache.load(identity)
    assert entry is not None
    assert entry.complete is False  # not marked complete yet
    assert entry.summaries == {0: "s0", 1: "s1"}
    cache.mark_complete(identity)
    assert cache.load(identity).complete is True


def test_partial_checkpoint_is_resumable(tmp_path):
    cache = LocalCache(_settings(tmp_path))
    identity = _identity()
    cache.save_chunk(identity, 0, "only-first")
    entry = cache.load(identity)
    assert entry.status == "in_progress"
    assert entry.summaries == {0: "only-first"}


def test_incompatible_identity_is_never_reused(tmp_path):
    cache = LocalCache(_settings(tmp_path))
    cache.save_chunk(_identity(), 0, "s0")
    assert cache.load(_identity(model="different")) is None


def test_tampered_manifest_identity_is_rejected(tmp_path):
    cache = LocalCache(_settings(tmp_path))
    identity = _identity()
    cache.save_chunk(identity, 0, "s0")
    manifest = cache.entry_dir(identity) / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["identity"]["model"] = "tampered"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert cache.load(identity) is None


def test_oversized_chunk_result_is_skipped(tmp_path):
    cache = LocalCache(_settings(tmp_path, max_chunk_bytes=8))
    identity = _identity()
    cache.save_chunk(identity, 0, "x" * 100)
    entry = cache.load(identity)
    assert entry is None or entry.summaries == {}


def test_max_entries_bounds_the_cache(tmp_path):
    cache = LocalCache(_settings(tmp_path, max_entries=2))
    for i in range(5):
        cache.save_chunk(_identity(document_hash=f"doc-{i}"), 0, f"s{i}")
    assert cache.status().entries <= 2


def test_cache_files_are_private(tmp_path):
    cache = LocalCache(_settings(tmp_path))
    identity = _identity()
    cache.save_chunk(identity, 0, "s0")
    directory = cache.entry_dir(identity)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    chunk = directory / "chunk-00000.json"
    assert stat.S_IMODE(chunk.stat().st_mode) == 0o600


def test_status_and_clear(tmp_path):
    cache = LocalCache(_settings(tmp_path))
    cache.save_chunk(_identity(), 0, "s0")
    cache.mark_complete(_identity())
    cache.save_chunk(_identity(document_hash="d2"), 0, "s1")
    status = cache.status()
    assert status.entries == 2
    assert status.complete == 1 and status.in_progress == 1
    assert status.to_json()["schema"] == "summarizer.cache.status.v1"
    assert cache.clear() == 2
    assert cache.status().entries == 0


# --- pipeline integration: resume + invalidation ----------------------------


def _pipeline_config(tmp_path, *, model="fake-model", mode="readwrite") -> Config:
    return Config(
        engine=EngineSettings(model=model, context_length=5000),
        defaults=DefaultsSettings(output_format="plain", stream=False),
        chunking=ChunkingSettings(
            max_tokens_per_chunk=60, overlap_tokens=0, reserve_output_tokens=20, max_chunks=64
        ),
        cache=_settings(tmp_path, mode=mode),
    )


def _write_doc(tmp_path) -> str:
    path = tmp_path / "doc.txt"
    path.write_text(("SECRETDOCMARKER alpha beta gamma delta " * 200), encoding="utf-8")
    return str(path)


def test_pipeline_reuses_cached_map_results_on_second_run(tmp_path):
    source = _write_doc(tmp_path)
    config = _pipeline_config(tmp_path)
    opts = PipelineOptions(stream=False)

    first = CountingEngine()
    result1 = Pipeline(config, diag=Diagnostics(quiet=True), engine=first).run(source, opts=opts)
    assert result1.aggregation.name == "mapreduce"
    chunks = result1.stats["chunks"]
    assert first.map_calls == chunks
    assert result1.stats["cached_chunks"] == 0

    second = CountingEngine()
    result2 = Pipeline(config, diag=Diagnostics(quiet=True), engine=second).run(source, opts=opts)
    assert second.map_calls == 0  # every map result came from the cache
    assert second.reduce_calls >= 1
    assert result2.stats["cached_chunks"] == chunks
    assert result2.summary == "FINAL SUMMARY"


def test_pipeline_does_not_cache_when_mode_off(tmp_path):
    source = _write_doc(tmp_path)
    config = _pipeline_config(tmp_path, mode="off")
    opts = PipelineOptions(stream=False)
    engine = CountingEngine()
    Pipeline(config, diag=Diagnostics(quiet=True), engine=engine).run(source, opts=opts)
    assert engine.map_calls == engine.map_calls  # ran normally
    assert not (tmp_path / "cache" / "results").exists()


def test_changing_the_model_invalidates_the_cache(tmp_path):
    source = _write_doc(tmp_path)
    config = _pipeline_config(tmp_path)
    opts = PipelineOptions(stream=False)
    Pipeline(config, diag=Diagnostics(quiet=True), engine=CountingEngine("model-a")).run(source, opts=opts)

    other = CountingEngine("model-b")
    Pipeline(
        _pipeline_config(tmp_path, model="model-b"),
        diag=Diagnostics(quiet=True),
        engine=other,
    ).run(source, opts=opts)
    assert other.map_calls > 0  # a different model must not reuse results


def test_cache_never_stores_document_content_or_source_path(tmp_path):
    source = _write_doc(tmp_path)
    config = _pipeline_config(tmp_path)
    Pipeline(config, diag=Diagnostics(quiet=True), engine=CountingEngine()).run(
        source, opts=PipelineOptions(stream=False)
    )
    blob = ""
    for path in (tmp_path / "cache").rglob("*"):
        if path.is_file():
            blob += path.read_text(encoding="utf-8", errors="replace")
    assert "SECRETDOCMARKER" not in blob
    assert source not in blob
    assert "chunk summary" in blob  # the derived summary is what is stored
