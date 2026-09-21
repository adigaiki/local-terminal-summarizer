"""The explicit summarization pipeline.

    Source -> Reader -> Document -> Chunker -> Profile -> Engine
            -> Aggregator -> Formatter -> stdout

Each stage is independently testable; cli.py stays thin and the pipeline
owns the ordering and the security-scoped prompt construction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from summarizer.aggregation import AggregationStrategy, MapReduce, choose_strategy
from summarizer.cache import CacheIdentity, LocalCache, content_hash
from summarizer.cancel import CancelToken
from summarizer.chunking import (
    Chunk,
    ChunkingDecision,
    budget_for,
    chunk_document,
    chunking_decision,
    estimate_tokens,
)
from summarizer.config import Config, EngineSettings, user_prompt_dir
from summarizer.document import Document, read_source
from summarizer.engine import Engine, EngineCapabilities, create_engine
from summarizer.engine.capabilities import CONTEXT_SOURCE_CONFIG, DEFAULT_CONTEXT_LENGTH
from summarizer.errors import InputError
from summarizer.log import Diagnostics
from summarizer.net import is_loopback_url
from summarizer.output import (
    build_json_envelope,
    dumps_json,
    format_markdown,
    format_plain,
    generate_json,
)
from summarizer.profiles import REDUCE_PROFILE, Profile, load_profile
from summarizer.prompt import PromptBuilder, profile_identity, prompt_identity
from summarizer.progress import ProgressReporter

__all__ = ["Pipeline", "PipelineOptions", "PipelineResult", "DryRunReport"]


@dataclass(frozen=True)
class PipelineOptions:
    profile: str = "plain"
    output_format: str = "markdown"
    model: str | None = None
    endpoint: str | None = None
    backend: str | None = None
    timeout_seconds: int | None = None
    retries: int | None = None
    stream: bool = True
    strict: bool = False
    chunk_strategy: str | None = None
    lang: str | None = None
    encoding: str | None = None
    context_file: str | None = None
    ocr: bool = False
    # Include an execution-stats object in the JSON envelope (stdout); without
    # this, stats are only reported by the CLI on stderr when --stats is set.
    stats: bool = False


@dataclass
class PipelineResult:
    text: str  # final output (formatted summary or full JSON envelope)
    summary: Any  # raw model output: str, or parsed JSON value
    document: Document
    profile: Profile
    decision: ChunkingDecision
    aggregation: AggregationStrategy
    engine: Engine
    boundary: str | None
    warnings: list[str]
    duration: float = 0.0
    output_format: str = "markdown"
    # Chunk provenance for aggregated runs (empty for a single-shot document).
    chunks: list[Chunk] = field(default_factory=list)
    # Execution statistics (numbers only; never document content).
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def chunk_provenance(self) -> list[dict[str, Any]]:
        total = len(self.chunks)
        return [chunk.provenance(total=total) for chunk in self.chunks]

    @property
    def engine_model(self) -> str:
        return self.engine.model

    @property
    def engine_backend(self) -> str:
        return self.engine.backend

    @property
    def engine_endpoint(self) -> str:
        return self.engine.endpoint


@dataclass
class DryRunReport:
    source: str | None
    document: Document
    profile: Profile
    decision: ChunkingDecision
    aggregation: AggregationStrategy
    engine: Engine
    prompt_structure: str
    prompt_sample: str
    # Which layer supplied each resolved engine value (never a value itself).
    origins: dict[str, str] = field(default_factory=dict)
    # Stable content-addressed identity of the effective profile/prompt.
    prompt_identity: str = ""
    concurrency: int = 1
    cache_mode: str = "off"

    def _origin(self, key: str) -> str:
        return self.origins.get(key, "built-in defaults")

    def render(self) -> str:
        from summarizer.log import redact_url

        doc = self.document
        lines = [
            "Dry run (no engine calls performed)",
            "",
            f"Input:          {self.source or 'stdin'}",
            f"  mime:         {doc.mime_type}",
            f"  encoding:     {doc.encoding or 'n/a'}",
            f"  bytes:        {doc.size}",
            f"  chars:        {doc.char_count}",
            f"  lines:        {doc.line_count}",
            f"  est. tokens:  ~{doc.token_estimate}",
        ]
        for key in ("language", "pages", "headings", "extracted_bytes", "pdf", "ocr", "warnings"):
            if key in doc.metadata:
                value = doc.metadata[key]
                if isinstance(value, list):
                    value = f"{len(value)} entries: {value[:3]}{'...' if len(value) > 3 else ''}"
                lines.append(f"  metadata.{key}: {value}")
        lines += [
            "",
            f"Profile:        {self.profile.name} ({self.profile.source})",
            f"  identity:     {self.prompt_identity or 'n/a'}",
            f"Engine:         {self.engine.backend}",
            f"Model:          {self.engine.model}",
            f"  source:       {self._origin('engine.model')}",
            f"Endpoint:       {redact_url(self.engine.endpoint)}",
            f"  source:       {self._origin('engine.endpoint')}",
            f"Max output:     {getattr(self.engine, 'max_tokens', 'n/a')} tokens/request",
            f"Reasoning:      {getattr(self.engine, 'reasoning_effort', 'n/a')} "
            f"({self._origin('engine.reasoning_effort')})",
            f"Concurrency:    {self.concurrency} "
            f"({self._origin('chunking.concurrency')})",
            f"Cache:          {self.cache_mode}",
            "",
            self.decision.describe(),
            f"Aggregation:    {self.aggregation.name} ({self.aggregation.reason})",
            "",
            "Prompt structure:",
            self.prompt_structure,
            "",
            "Prompt sample (truncated):",
            self.prompt_sample,
            "",
            "No network request",
            "No LLM request",
        ]
        return "\n".join(lines)


class Pipeline:
    """Orchestrates one summarization request end to end."""

    def __init__(
        self,
        config: Config,
        *,
        diag: Diagnostics,
        engine: Engine | None = None,
        builder: PromptBuilder | None = None,
        cancel: CancelToken | None = None,
        progress: ProgressReporter | None = None,
    ) -> None:
        self.config = config
        self.diag = diag
        self._engine = engine
        self.builder = builder or PromptBuilder(diag=diag)
        self.cancel = cancel or CancelToken()
        self.progress = progress

    # -- cancellation ---------------------------------------------------------

    def _check_cancelled(self) -> None:
        self.cancel.raise_if_cancelled()

    # -- engine resolution ----------------------------------------------------

    def _effective_engine_settings(self, opts: PipelineOptions) -> EngineSettings:
        current = self.config.engine
        overrides: dict[str, Any] = {}
        if opts.model:
            overrides["model"] = opts.model
        if opts.endpoint:
            overrides["endpoint"] = opts.endpoint
        if opts.backend:
            overrides["backend"] = opts.backend
        if opts.timeout_seconds:
            overrides["timeout_seconds"] = opts.timeout_seconds
        if opts.retries is not None:
            overrides["retries"] = opts.retries
        if not overrides:
            return current
        return EngineSettings(**{**current.__dict__, **overrides})

    def resolve_engine(self, opts: PipelineOptions) -> Engine:
        if self._engine is not None:
            return self._engine
        settings = self._effective_engine_settings(opts)
        return create_engine(self.config.with_overrides(engine=settings), diag=self.diag)

    def _safe_capabilities(self, engine: Engine) -> EngineCapabilities:
        """Capabilities may require a server probe; degrade gracefully."""
        try:
            return engine.capabilities()
        except Exception as exc:  # server down/unknown format: never fatal here
            self.diag.verbose_warn(f"capability detection unavailable ({exc}); using defaults")
            return EngineCapabilities()

    # -- reading --------------------------------------------------------------

    def _read(self, source: str | None, opts: PipelineOptions) -> Document:
        options: dict[str, Any] = {
            "max_bytes": self.config.input.max_bytes,
            "max_lines": self.config.input.max_lines,
            "encoding": opts.encoding or self.config.input.encoding,
            "max_pdf_pages": self.config.input.max_pdf_pages,
            "max_extracted_bytes": self.config.input.max_extracted_bytes,
            "ocr_timeout_seconds": self.config.input.ocr_timeout_seconds,
            "ocr": opts.ocr,
        }
        document = read_source(source, diag=self.diag, options=options)
        if not document.content.strip():
            raise InputError(
                "no input provided",
                hint="pipe input or provide a file, e.g. `summarize notes.md`",
            )
        return document

    def _read_context(self, path: str | None) -> str | None:
        if not path:
            return None
        from summarizer.document.reader import checked_size, decode_bytes

        file_path = Path(path)
        if not file_path.is_file():
            raise InputError(f"context file not found: {path}")
        checked_size(file_path, self.config.input.max_bytes, what="context file")
        raw = file_path.read_bytes()
        return decode_bytes(raw, self.config.input.encoding, diag=self.diag, what="context file")

    # -- output helpers --------------------------------------------------------

    def _format_text(self, summary: str, fmt: str) -> str:
        if fmt == "plain":
            return format_plain(summary)
        return format_markdown(summary)

    def _build_envelope(
        self,
        *,
        summary: Any,
        document: Document,
        profile: Profile,
        engine: Engine,
        decision: ChunkingDecision,
        aggregation: AggregationStrategy,
        boundary: str | None,
        warnings: list[str],
        duration: float,
        chunk_provenance: list[dict[str, Any]] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> str:
        envelope = build_json_envelope(
            summary,
            document=document,
            profile=profile.name,
            engine_backend=engine.backend,
            model=engine.model,
            endpoint=engine.endpoint,
            boundary=boundary,
            strategy=aggregation.name,
            chunk_count=len(chunk_provenance) if chunk_provenance else 1,
            warnings=warnings,
            duration_seconds=duration,
            chunk_provenance=chunk_provenance,
        )
        if stats:
            envelope["stats"] = stats
        return dumps_json(envelope)

    # -- prompts ---------------------------------------------------------------

    def _build_single_prompt(self, profile, content: str, opts: PipelineOptions, context: str | None):
        return self.builder.build(
            profile=profile,
            document_text=content,
            lang=opts.lang,
            output_format=opts.output_format,
            context=context,
        )

    # -- main entry -----------------------------------------------------------

    def run(
        self,
        source: str | None,
        *,
        opts: PipelineOptions,
        emit: Callable[[str], None] | None = None,
    ) -> PipelineResult:
        start = time.monotonic()
        self._check_cancelled()
        document = self._read(source, opts)
        profile = load_profile(opts.profile, user_dir=user_prompt_dir(), diag=self.diag)
        if opts.output_format not in ("plain", "markdown", "json"):
            raise InputError(
                f"unknown output format {opts.output_format!r}",
                hint="expected one of: plain, markdown, json",
            )
        engine = self.resolve_engine(opts)
        if hasattr(engine, "set_cancel"):
            engine.set_cancel(self.cancel)
        max_output_tokens = getattr(engine, "max_tokens", self.config.engine.max_tokens)
        capabilities = self._safe_capabilities(engine)
        context = self._read_context(opts.context_file)
        self._check_cancelled()

        if self.diag.verbose:
            self.diag.verbose_message(f"capabilities: {capabilities.describe()}")
            if not is_loopback_url(engine.endpoint):
                self.diag.verbose_warn(
                    f"engine endpoint {engine.endpoint} is not loopback; "
                    "document content will leave this machine"
                )

        # The trusted --context file competes for the same context window, so
        # its size is part of the budget rather than an afterthought.
        context_file_tokens = estimate_tokens(context) if context else 0
        decision = chunking_decision(
            document.content,
            capabilities=capabilities,
            settings=self.config.chunking,
            max_output_tokens=max_output_tokens,
            profile=profile,
            config_context_length=self.config.engine.context_length,
            explicit_unit=opts.chunk_strategy or self.config.defaults.chunk_strategy,
            strict=opts.strict,
            context_file_tokens=context_file_tokens,
            max_chunks=self.config.chunking.max_chunks,
        )
        aggregation = choose_strategy(decision)
        chunks = chunk_document(
            document.content,
            decision=decision,
            settings=self.config.chunking,
            source=document.source,
        )
        chunks = document.annotate_chunks(chunks)
        boundary: str | None = None
        self._check_cancelled()

        self.diag.progress(decision.describe())
        if self.diag.verbose and decision.budget is not None:
            self.diag.verbose_message(f"budget: {decision.budget.describe()}")

        # Capability negotiation: stream only when the caller asked for it and
        # the engine reports support. No backend name is consulted here.
        stream_requested = bool(opts.stream and opts.output_format != "json")
        effective_stream = stream_requested and capabilities.supports_streaming
        if stream_requested and not capabilities.supports_streaming:
            self.diag.verbose_warn(
                "engine reports no streaming support; generating in a single request"
            )

        stats: dict[str, Any] = {
            "input_bytes": document.size,
            "input_chars": document.char_count,
            "input_tokens_est": document.token_estimate,
            "chunks": len(chunks),
            "strategy": aggregation.name,
            "concurrency": self.config.chunking.concurrency,
        }
        label = "stdin" if document.source == "stdin" else Path(document.source).name
        reporter = self.progress
        if reporter is not None:
            reporter.start(label=label, model=engine.model, total_chunks=len(chunks))
        generation_started = time.monotonic()
        try:
            if decision.single_shot:
                prompt = self._build_single_prompt(profile, chunks[0].text, opts, context)
                boundary = prompt.boundary
                summary, text = self._execute_single(
                    prompt=prompt,
                    engine=engine,
                    capabilities=capabilities,
                    fmt=opts.output_format,
                    stream=effective_stream,
                    emit=emit,
                )
                stats["generation_seconds"] = round(time.monotonic() - generation_started, 3)
            else:
                summary, text, boundary = self._execute_map_reduce(
                    chunks=chunks,
                    document=document,
                    decision=decision,
                    profile=profile,
                    context=context,
                    opts=opts,
                    engine=engine,
                    capabilities=capabilities,
                    stream=effective_stream,
                    emit=emit,
                    stats=stats,
                )
        finally:
            if reporter is not None:
                reporter.finish()

        self._check_cancelled()
        warnings = self.diag.take_warnings()
        duration = time.monotonic() - start
        stats["total_seconds"] = round(duration, 3)
        generated = stats.get("generated_tokens_est")
        gen_seconds = stats.get("generation_seconds")
        if generated and gen_seconds:
            stats["tokens_per_second_est"] = round(generated / gen_seconds, 1)
        provenance = (
            [chunk.provenance(total=len(chunks)) for chunk in chunks]
            if len(chunks) > 1
            else None
        )
        if opts.output_format == "json":
            text = self._build_envelope(
                summary=summary,
                document=document,
                profile=profile,
                engine=engine,
                decision=decision,
                aggregation=aggregation,
                boundary=boundary,
                warnings=warnings,
                duration=duration,
                chunk_provenance=provenance,
                stats=stats if opts.stats else None,
            )
        else:
            text = self._format_text(text if isinstance(text, str) else str(text), opts.output_format)
        return PipelineResult(
            text=text,
            summary=summary,
            document=document,
            profile=profile,
            decision=decision,
            aggregation=aggregation,
            engine=engine,
            boundary=boundary,
            warnings=warnings,
            duration=duration,
            output_format=opts.output_format,
            chunks=chunks if len(chunks) > 1 else [],
            stats=stats,
        )

    # -- execution paths ------------------------------------------------------

    @staticmethod
    def _json_request(engine: Engine):
        """Adapt an engine to the formatter's engine-independent callback."""

        def request(prompt: str, *, json_object: bool = False) -> str:
            return engine.generate(prompt=prompt, json_object=json_object)

        return request

    def _execute_single(
        self,
        *,
        prompt,
        engine: Engine,
        capabilities: EngineCapabilities,
        fmt: str,
        stream: bool,
        emit: Callable[[str], None] | None,
    ) -> tuple[Any, str]:
        self._check_cancelled()
        if fmt == "json":
            value = generate_json(
                self._json_request(engine),
                prompt.text,
                diag=self.diag,
                structured_supported=capabilities.structured_json,
            )
            return value, str(value)
        if stream:
            collected = self._stream_collect(engine.stream(prompt=prompt.text), emit)
            return collected, collected
        text = engine.generate(prompt=prompt.text)
        return text, text

    def _note(self, message: str) -> None:
        if self.progress is not None:
            self.progress.note(message)
        else:
            self.diag.progress(message)

    def _cache_identity(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
        decision: ChunkingDecision,
        profile: Profile,
        opts: PipelineOptions,
        engine: Engine,
        context: str | None,
    ) -> CacheIdentity | None:
        cache = LocalCache(self.config.cache, diag=self.diag)
        if not cache.enabled:
            return None
        return CacheIdentity(
            document_hash=content_hash(document.content),
            model=engine.model,
            backend=engine.backend,
            profile=profile.name,
            profile_identity=profile_identity(profile),
            prompt_identity=prompt_identity(
                profile,
                lang=opts.lang,
                output_format=opts.output_format,
                has_context=bool(context),
            ),
            output_format=opts.output_format,
            lang=opts.lang or "",
            context_hash=content_hash(context) if context else "none",
            chunk_hashes=tuple(content_hash(chunk.text) for chunk in chunks),
            chunking={
                "unit": decision.unit,
                "max_chunk": decision.max_chunk,
                "max_tokens_per_chunk": self.config.chunking.max_tokens_per_chunk,
                "overlap_tokens": self.config.chunking.overlap_tokens,
                "reserve_output_tokens": self.config.chunking.reserve_output_tokens,
            },
            engine={
                "max_tokens": getattr(engine, "max_tokens", 0),
                "temperature": getattr(engine, "temperature", None),
                "reasoning_effort": getattr(engine, "reasoning_effort", None),
                "context_length": self.config.engine.context_length,
            },
        )

    def _execute_map_reduce(
        self,
        *,
        chunks: list[Chunk],
        document: Document,
        decision: ChunkingDecision,
        profile: Profile,
        context: str | None,
        opts: PipelineOptions,
        engine: Engine,
        capabilities: EngineCapabilities,
        emit: Callable[[str], None] | None,
        stream: bool | None = None,
        stats: dict[str, Any] | None = None,
    ) -> tuple[Any, str, str]:
        effective_stream = opts.stream if stream is None else stream
        stats = stats if stats is not None else {}
        reduce_profile = load_profile(REDUCE_PROFILE, user_dir=user_prompt_dir(), diag=self.diag)
        mr = MapReduce(engine, self.builder, diag=self.diag, reduce_profile=reduce_profile)
        self.diag.progress(
            f"document too large: {len(chunks)} chunk(s); using map-reduce"
        )

        # Optional cache/checkpoint: reuse compatible completed map results.
        identity = self._cache_identity(
            document=document, chunks=chunks, decision=decision,
            profile=profile, opts=opts, engine=engine, context=context,
        )
        cache = LocalCache(self.config.cache, diag=self.diag)
        cached: dict[int, str] = {}
        if identity is not None and cache.read_enabled:
            entry = cache.load(identity)
            if entry and entry.summaries:
                cached = dict(entry.summaries)
                state = "reusing" if entry.complete else "resuming from checkpoint"
                self._note(
                    f"cache: {state} — {len(cached)}/{len(chunks)} chunk summaries"
                )
        stats["cached_chunks"] = len(cached)

        completed = [len(cached)]
        generated_tokens = [0]

        def on_result(index: int, summary: str) -> None:
            generated_tokens[0] += estimate_tokens(summary)
            if identity is not None and cache.write_enabled:
                cache.save_chunk(identity, index, summary)
            completed[0] += 1
            if self.progress is not None:
                self.progress.update(completed[0])

        map_started = time.monotonic()
        summaries = mr.run_map(
            chunks,
            profile=profile,
            context=context,
            lang=opts.lang,
            output_format=opts.output_format,
            concurrency=self.config.chunking.concurrency,
            cancel=self.cancel,
            on_result=on_result,
            cached=cached,
        )
        stats["map_seconds"] = round(time.monotonic() - map_started, 3)

        if (
            identity is not None
            and cache.write_enabled
            and len(summaries) == len(chunks)
        ):
            cache.mark_complete(identity)

        # The interim summaries must themselves fit the reduce prompt. For very
        # long documents they do not, so reduce in as many rounds as needed.
        reduce_started = time.monotonic()
        reduce_budget = budget_for(
            capabilities,
            self.config.chunking,
            reduce_profile,
            max_output_tokens=getattr(engine, "max_tokens", self.config.engine.max_tokens),
            config_context_length=self.config.engine.context_length,
        ).usable_input_tokens
        summaries = mr.consolidate(
            summaries,
            lang=opts.lang,
            output_format=opts.output_format,
            usable_tokens=reduce_budget,
        )

        if opts.output_format == "json":
            prompt = self.builder.build_reduce(
                profile=reduce_profile,
                summaries=summaries,
                lang=opts.lang,
                output_format="json",
            )
            self._check_cancelled()
            value = generate_json(
                self._json_request(engine),
                prompt.text,
                diag=self.diag,
                structured_supported=capabilities.structured_json,
            )
            stats["reduce_seconds"] = round(time.monotonic() - reduce_started, 3)
            stats["generated_tokens_est"] = generated_tokens[0] + estimate_tokens(str(value))
            stats["generation_seconds"] = round(
                stats.get("map_seconds", 0.0) + stats["reduce_seconds"], 3
            )
            return value, str(value), prompt.boundary

        reduce_prompt = self.builder.build_reduce(
            profile=reduce_profile,
            summaries=summaries,
            lang=opts.lang,
            output_format=opts.output_format,
        )
        self._check_cancelled()
        if effective_stream:
            collected = self._stream_collect(engine.stream(prompt=reduce_prompt.text), emit)
            final = collected
        else:
            final = engine.generate(prompt=reduce_prompt.text)
        stats["reduce_seconds"] = round(time.monotonic() - reduce_started, 3)
        stats["generated_tokens_est"] = generated_tokens[0] + estimate_tokens(final)
        stats["generation_seconds"] = round(
            stats.get("map_seconds", 0.0) + stats["reduce_seconds"], 3
        )
        return final, final, reduce_prompt.boundary

    def _stream_collect(
        self,
        tokens: Iterator[str],
        emit: Callable[[str], None] | None,
    ) -> str:
        """Consume a token stream, emitting when possible, and return the text."""
        from summarizer.errors import Interrupted

        parts: list[str] = []
        try:
            for delta in tokens:
                self._check_cancelled()
                parts.append(delta)
                if emit:
                    emit(delta)
        except KeyboardInterrupt as exc:
            raise Interrupted("interrupted") from exc
        return "".join(parts)

    # -- dry run --------------------------------------------------------------

    def dry_run(self, source: str | None, *, opts: PipelineOptions) -> DryRunReport:
        document = self._read(source, opts)
        profile = load_profile(opts.profile, user_dir=user_prompt_dir(), diag=self.diag)
        engine = self.resolve_engine(opts)

        # Deliberately do NOT probe the live server: a dry run must never
        # contact a model or network. Use the configured value or an honest
        # "unknown" so the report never implies knowledge we do not have.
        configured = self.config.engine.context_length or 0
        spec = getattr(engine, "spec", None)
        caps = EngineCapabilities(
            backend=engine.backend,
            streaming=bool(getattr(spec, "streaming", True)),
            structured_json=None,
            model_listing=bool(getattr(spec, "model_listing", True)),
            context_length=configured or None,
            context_source=CONTEXT_SOURCE_CONFIG if configured else None,
            reasoning_control=getattr(spec, "reasoning_control", None),
        )
        context = self._read_context(opts.context_file)
        context_file_tokens = estimate_tokens(context) if context else 0
        decision = chunking_decision(
            document.content,
            capabilities=caps,
            settings=self.config.chunking,
            max_output_tokens=getattr(engine, "max_tokens", self.config.engine.max_tokens),
            profile=profile,
            config_context_length=self.config.engine.context_length,
            explicit_unit=opts.chunk_strategy or self.config.defaults.chunk_strategy,
            strict=opts.strict,
            context_file_tokens=context_file_tokens,
            max_chunks=self.config.chunking.max_chunks,
        )
        aggregation = choose_strategy(decision)

        chunks = chunk_document(
            document.content,
            decision=decision,
            settings=self.config.chunking,
            source=document.source,
        )
        if decision.single_shot:
            built = self._build_single_prompt(profile, chunks[0].text, opts, context)
        else:
            reduce_profile = load_profile(REDUCE_PROFILE, user_dir=user_prompt_dir(), diag=self.diag)
            built = self.builder.build_reduce(
                profile=reduce_profile,
                summaries=["<chunk summary placeholder>"] * min(3, len(chunks)),
                lang=opts.lang,
                output_format=opts.output_format,
            )
        section_lines = [
            f"  [{key}] approx {len(value)} chars" for key, value in built.sections.items()
        ]
        structure = "\n".join(
            [
                "  TRUSTED: security preamble",
                "  TRUSTED: profile instructions",
                *section_lines,
                f"  boundary token: {built.boundary}",
            ]
        )
        sample = built.text
        if len(sample) > 1400:
            sample = sample[:1400] + f"\n... [truncated {len(built.text) - 1400} chars]"
        return DryRunReport(
            source=source or (document.source if document.source != "stdin" else "stdin"),
            document=document,
            profile=profile,
            decision=decision,
            aggregation=aggregation,
            engine=engine,
            prompt_structure=structure,
            prompt_sample=sample,
            origins=dict(self.config.origins),
            prompt_identity=prompt_identity(
                profile,
                lang=opts.lang,
                output_format=opts.output_format,
                has_context=bool(context),
            ),
            concurrency=self.config.chunking.concurrency,
            cache_mode=self.config.cache.mode,
        )
