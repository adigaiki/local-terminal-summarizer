"""Map-reduce aggregation for long documents.

    document -> chunks -> per-chunk summaries -> final reduce summary

Map: each chunk is summarized independently using the request profile.
Reduce: the interim summaries are combined by a *separate* reduce profile
(``__reduce__``) which continues to treat them as untrusted data.

Long documents need more than one reduce step: once a document is split into
enough chunks, the interim summaries themselves no longer fit in the model's
context window. :meth:`MapReduce.consolidate` therefore reduces in *rounds* --
combining groups of summaries until the remainder fits a single final prompt.
Every round keeps the same invariant: interim summaries and document content
are always wrapped inside a fresh, per-round random boundary and are never
interpreted as instructions.

Nothing here concatenates interim summaries into the answer: they are always
re-combined by the model under a distinct instruction set.
"""

from __future__ import annotations

from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from typing import Callable, Iterator, Sequence

from summarizer.cancel import CancelToken
from summarizer.chunking.splitter import Chunk
from summarizer.chunking.token import estimate_chars_for_tokens, estimate_tokens
from summarizer.engine.base import Engine
from summarizer.log import Diagnostics
from summarizer.profiles import Profile
from summarizer.prompt.builder import PromptBuilder

__all__ = [
    "MapReduce",
    "plan_reduce_groups",
    "truncate_to_tokens",
    "REDUCE_SUMMARY_OVERHEAD_TOKENS",
]

# Boundary tags plus separators cost tokens too; budget for them so a group of
# summaries that "exactly fits" does not overflow once wrapped.
REDUCE_SUMMARY_OVERHEAD_TOKENS = 24

# Upper bound on reduce rounds. Each round strictly shrinks the summary count,
# so this is a safety valve rather than a normal exit.
MAX_REDUCE_ROUNDS = 8


def plan_reduce_groups(
    summaries: Sequence[str],
    *,
    usable_tokens: int,
    per_summary_overhead_tokens: int = REDUCE_SUMMARY_OVERHEAD_TOKENS,
    measure=estimate_tokens,
) -> list[list[str]]:
    """Pack interim summaries into groups that fit ``usable_tokens``.

    Pure function: no engine, no I/O. A summary that is itself larger than the
    budget gets a group of its own (the caller is responsible for bounding it).
    """
    if usable_tokens < 1:
        return [list(summaries)]
    groups: list[list[str]] = []
    current: list[str] = []
    used = 0
    for summary in summaries:
        cost = measure(summary) + per_summary_overhead_tokens
        if current and used + cost > usable_tokens:
            groups.append(current)
            current = []
            used = 0
        current.append(summary)
        used += cost
    if current:
        groups.append(current)
    return groups or [[]]


def truncate_to_tokens(text: str, limit_tokens: int) -> str:
    """Word-aligned truncation used only when one summary cannot fit at all.

    Never silent: callers warn when they use this. The result always satisfies
    :func:`estimate_tokens` against ``limit_tokens`` -- the chars/4 conversion
    is only a first guess, and the estimator also counts words, so the clip is
    verified (and binary-searched tighter if needed) before returning.
    """
    if limit_tokens <= 0:
        return ""
    if estimate_tokens(text) <= limit_tokens:
        return text
    char_budget = max(1, estimate_chars_for_tokens(limit_tokens))
    clipped = text[:char_budget]
    cut = clipped.rfind(" ")
    if cut > 0:
        clipped = clipped[:cut]
    if estimate_tokens(clipped) > limit_tokens:
        # Longest prefix that really fits under the estimator.
        lo, hi = 0, len(clipped)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if estimate_tokens(text[:mid]) <= limit_tokens:
                lo = mid
            else:
                hi = mid - 1
        clipped = text[:lo]
        cut = clipped.rfind(" ")
        if cut > 0 and estimate_tokens(clipped[:cut]) > 0:
            clipped = clipped[:cut]
    return clipped


class MapReduce:
    def __init__(
        self,
        engine: Engine,
        builder: PromptBuilder,
        *,
        diag: Diagnostics,
        reduce_profile: Profile,
    ) -> None:
        self.engine = engine
        self.builder = builder
        self.diag = diag
        self.reduce_profile = reduce_profile

    # -- map ---------------------------------------------------------------

    def map_step(
        self,
        chunk: Chunk,
        *,
        profile: Profile,
        context: str | None,
        lang: str | None,
        output_format: str,
        cancel: CancelToken | None = None,
    ) -> str:
        if cancel is not None:
            cancel.raise_if_cancelled()
        prompt = self.builder.build(
            profile=profile,
            document_text=chunk.text,
            context=context,
            lang=lang,
            output_format=output_format,
        )
        self.diag.progress(
            f"map: summarizing chunk {chunk.index + 1} "
            f"(chars {chunk.start_char}-{chunk.end_char}, "
            f"boundary {chunk.boundary})..."
        )
        return self.engine.generate(prompt=prompt.text, temperature=0.2)

    def run_map(
        self,
        chunks: list[Chunk],
        *,
        profile: Profile,
        context: str | None,
        lang: str | None,
        output_format: str,
        concurrency: int = 1,
        cancel: CancelToken | None = None,
        on_result: Callable[[int, str], None] | None = None,
        cached: dict[int, str] | None = None,
    ) -> list[str]:
        """Run the map stage for all chunks, returning interim summaries.

        ``concurrency`` > 1 runs independent chunks in a bounded thread pool.
        Results are always assembled in chunk order regardless of completion
        order, so provenance and the reduce stage are deterministic. The
        default is 1, which is exactly the original sequential behavior.

        ``cached`` supplies already-completed summaries to reuse (checkpoint/
        resume); ``on_result`` is called for each newly generated result so
        the caller can persist it. Failures are collected and the lowest-index
        failure is raised, after cancelling pending work.
        """
        cached = cached or {}
        total = len(chunks)
        summaries: list[str | None] = [None] * total
        for index, summary in cached.items():
            if 0 <= index < total:
                summaries[index] = summary

        pending = [chunk for chunk in chunks if chunk.index not in cached]
        if not pending:
            return [summary if summary is not None else "" for summary in summaries]

        if concurrency <= 1:
            for chunk in pending:
                if cancel is not None:
                    cancel.raise_if_cancelled()
                summary = self.map_step(
                    chunk,
                    profile=profile,
                    context=context,
                    lang=lang,
                    output_format=output_format,
                    cancel=cancel,
                )
                summaries[chunk.index] = summary
                if on_result is not None:
                    on_result(chunk.index, summary)
        else:
            self._run_map_parallel(
                pending,
                summaries,
                profile=profile,
                context=context,
                lang=lang,
                output_format=output_format,
                concurrency=concurrency,
                cancel=cancel,
                on_result=on_result,
            )
        return [summary if summary is not None else "" for summary in summaries]

    def _run_map_parallel(
        self,
        pending: list[Chunk],
        summaries: list[str | None],
        *,
        profile: Profile,
        context: str | None,
        lang: str | None,
        output_format: str,
        concurrency: int,
        cancel: CancelToken | None,
        on_result: Callable[[int, str], None] | None,
    ) -> None:
        """Bounded parallel map. Deterministic ordering and error selection."""
        errors: dict[int, BaseException] = {}

        def worker(chunk: Chunk) -> str:
            if cancel is not None:
                cancel.raise_if_cancelled()
            return self.map_step(
                chunk,
                profile=profile,
                context=context,
                lang=lang,
                output_format=output_format,
                cancel=cancel,
            )

        futures = {}
        pool = ThreadPoolExecutor(max_workers=max(1, concurrency), thread_name_prefix="summarize-map")
        try:
            for chunk in pending:
                if cancel is not None:
                    cancel.raise_if_cancelled()
                futures[pool.submit(worker, chunk)] = chunk.index
            for future in as_completed(futures):
                index = futures[future]
                try:
                    summary = future.result()
                except CancelledError:
                    continue
                except BaseException as exc:  # collected, not raised per-thread
                    errors[index] = exc
                    for pending_future in futures:
                        pending_future.cancel()
                    continue
                summaries[index] = summary
                if on_result is not None:
                    on_result(index, summary)
        finally:
            # Do not wait for queued work that has not started.
            pool.shutdown(wait=True, cancel_futures=True)
        if errors:
            # Deterministic: always report the earliest chunk that failed.
            first = min(errors)
            raise errors[first]

    # -- reduce ------------------------------------------------------------

    def reduce_step(
        self,
        summaries: list[str],
        *,
        lang: str | None,
        output_format: str,
        json_object: bool = False,
        stream: bool = False,
    ) -> str | Iterator[str]:
        """Combine interim summaries. Never used for a single chunk."""
        prompt = self.builder.build_reduce(
            profile=self.reduce_profile,
            summaries=summaries,
            lang=lang,
            output_format=output_format,
        )
        self.diag.progress(f"reduce: combining {len(summaries)} interim summaries...")
        if stream:
            return self._stream_reduce(prompt.text, json_object=json_object)
        return self.engine.generate(prompt=prompt.text, json_object=json_object, temperature=0.2)

    def _stream_reduce(self, prompt_text: str, *, json_object: bool) -> Iterator[str]:
        # Streaming the reduce step is fine (JSON output is never streamed:
        # the pipeline disables streaming for JSON).
        yield from self.engine.stream(prompt=prompt_text)

    def consolidate(
        self,
        summaries: list[str],
        *,
        lang: str | None,
        output_format: str,
        usable_tokens: int,
    ) -> list[str]:
        """Reduce in rounds until the remainder fits one final reduce prompt.

        Every round is itself a reduce stage, so each group of interim
        summaries is wrapped in its own freshly generated untrusted boundary
        (see :meth:`PromptBuilder.build_reduce`).
        """
        current = self._bound_oversized(summaries, usable_tokens=usable_tokens)
        if usable_tokens < 1:
            from summarizer.errors import InputError
            raise InputError(
                "reduce budget is too small to combine interim summaries",
                hint="raise the model context window or lower reserve_output_tokens",
            )
        rounds = 0
        while len(current) > 1 and rounds < MAX_REDUCE_ROUNDS:
            groups = plan_reduce_groups(current, usable_tokens=usable_tokens)
            if len(groups) <= 1:
                break  # the remainder fits a single final reduce prompt
            rounds += 1
            self.diag.progress(
                f"reduce round {rounds}: {len(current)} interim summaries "
                f"do not fit one prompt; combining in {len(groups)} groups..."
            )
            current = [
                str(self.reduce_step(group, lang=lang, output_format=output_format))
                for group in groups
            ]
            current = self._bound_oversized(current, usable_tokens=usable_tokens)
        if len(plan_reduce_groups(current, usable_tokens=usable_tokens)) > 1:
            from summarizer.errors import InputError
            raise InputError(
                "reduce did not converge within its context budget",
                hint="lower engine.max_tokens or use a larger context window",
            )
        # Final invariant: the last reduce prompt must itself fit the budget.
        # _bound_oversized guarantees single summaries fit, but a *group* of
        # them can still exceed the budget (a small budget with several
        # summaries); refusing loudly beats silently overflowing the window.
        for group in plan_reduce_groups(current, usable_tokens=usable_tokens):
            if sum(estimate_tokens(s) for s in group) + REDUCE_SUMMARY_OVERHEAD_TOKENS > usable_tokens:
                from summarizer.errors import InputError
                raise InputError(
                    "interim summaries exceed the reduce budget even after "
                    "truncation; refusing to overflow the context window",
                    hint="lower engine.max_tokens, or use a larger context window",
                )
        return current

    def _bound_oversized(self, summaries: list[str], *, usable_tokens: int) -> list[str]:
        """Truncate a single summary that cannot fit the reduce budget alone.

        This is the only lossy step in aggregation, and it is never silent.
        """
        if usable_tokens < 1:
            return summaries
        bounded: list[str] = []
        for index, summary in enumerate(summaries, start=1):
            if estimate_tokens(summary) + REDUCE_SUMMARY_OVERHEAD_TOKENS <= usable_tokens:
                bounded.append(summary)
                continue
            clipped = truncate_to_tokens(summary, usable_tokens - REDUCE_SUMMARY_OVERHEAD_TOKENS)
            self.diag.warn(
                f"interim summary {index} exceeds the reduce budget; "
                "truncating it to fit the model context window"
            )
            bounded.append(clipped)
        return bounded
