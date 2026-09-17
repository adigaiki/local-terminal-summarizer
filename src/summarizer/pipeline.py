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
from summarizer.prompt import PromptBuilder

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

    @property
    def chunk_provenance(self) -> list[dict[str, Any]]:
        total = len(self.chunks)
        return [chunk.provenance(total=total) for chunk in self.chunks]

    @property
    def engine_model(self) -> str:
        return self.engine.model
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

    def render(self) -> str:
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
        for key in ("language", "pages", "headings"):
            if key in doc.metadata:
                value = doc.metadata[key]
                if isinstance(value, list):
                    value = f"{len(value)} entries: {value[:3]}{'...' if len(value) > 3 else ''}"
                lines.append(f"  metadata.{key}: {value}")
        lines += [
            "",
            f"Profile:        {self.profile.name} ({self.profile.source})",
            f"Engine:         {self.engine.backend}",
            f"Model:          {self.engine.model}",
            f"Endpoint:       {self.engine.endpoint}",
            f"Max output:     {getattr(self.engine, 'max_tokens', 'n/a')} tokens/request",
            f"Reasoning:      {getattr(self.engine, 'reasoning_effort', 'n/a')}",
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
    ) -> None:
        self.config = config
        self.diag = diag
        self._engine = engine
        self.builder = builder or PromptBuilder(diag=diag)

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
            chunk_count=decision.chunk_count,
            warnings=warnings,
            duration_seconds=duration,
            chunk_provenance=chunk_provenance,
        )
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
        document = self._read(source, opts)
        profile = load_profile(opts.profile, user_dir=user_prompt_dir(), diag=self.diag)
        if opts.output_format not in ("plain", "markdown", "json"):
            raise InputError(
                f"unknown output format {opts.output_format!r}",
                hint="expected one of: plain, markdown, json",
            )
        engine = self.resolve_engine(opts)
        capabilities = self._safe_capabilities(engine)
        context = self._read_context(opts.context_file)

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
        boundary: str | None = None

        self.diag.progress(decision.describe())
        if self.diag.verbose and decision.budget is not None:
            self.diag.verbose_message(f"budget: {decision.budget.describe()}")

        if decision.single_shot:
            prompt = self._build_single_prompt(profile, chunks[0].text, opts, context)
            boundary = prompt.boundary
            summary, text = self._execute_single(
                prompt=prompt,
                engine=engine,
                capabilities=capabilities,
                fmt=opts.output_format,
                stream=opts.stream,
                emit=emit,
            )
        else:
            summary, text, boundary = self._execute_map_reduce(
                chunks=chunks,
                profile=profile,
                context=context,
                opts=opts,
                engine=engine,
                capabilities=capabilities,
                emit=emit,
            )

        warnings = self.diag.take_warnings()
        duration = time.monotonic() - start
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

    def _execute_map_reduce(
        self,
        *,
        chunks: list[Chunk],
        profile: Profile,
        context: str | None,
        opts: PipelineOptions,
        engine: Engine,
        capabilities: EngineCapabilities,
        emit: Callable[[str], None] | None,
    ) -> tuple[Any, str, str]:
        reduce_profile = load_profile(REDUCE_PROFILE, user_dir=user_prompt_dir(), diag=self.diag)
        mr = MapReduce(engine, self.builder, diag=self.diag, reduce_profile=reduce_profile)
        self.diag.progress(
            f"document too large: {len(chunks)} chunk(s); using map-reduce"
        )
        summaries = mr.run_map(
            chunks,
            profile=profile,
            context=context,
            lang=opts.lang,
            output_format=opts.output_format,
        )

        # The interim summaries must themselves fit the reduce prompt. For very
        # long documents they do not, so reduce in as many rounds as needed.
        reduce_budget = budget_for(
            capabilities,
            self.config.chunking,
            reduce_profile,
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
            value = generate_json(
                self._json_request(engine),
                prompt.text,
                diag=self.diag,
                structured_supported=capabilities.structured_json,
            )
            return value, str(value), prompt.boundary

        reduce_prompt = self.builder.build_reduce(
            profile=reduce_profile,
            summaries=summaries,
            lang=opts.lang,
            output_format=opts.output_format,
        )
        if opts.stream:
            collected = self._stream_collect(engine.stream(prompt=reduce_prompt.text), emit)
            return collected, collected, reduce_prompt.boundary
        text = engine.generate(prompt=reduce_prompt.text)
        return text, text, reduce_prompt.boundary

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
        caps = EngineCapabilities(
            streaming=True,
            structured_json=None,
            model_listing=True,
            context_length=configured or None,
            context_source=CONTEXT_SOURCE_CONFIG if configured else None,
        )
        context = self._read_context(opts.context_file)
        context_file_tokens = estimate_tokens(context) if context else 0
        decision = chunking_decision(
            document.content,
            capabilities=caps,
            settings=self.config.chunking,
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
        )
