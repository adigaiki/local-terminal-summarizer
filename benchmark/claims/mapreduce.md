# Reference claims — mapreduce.py

Source: `src/summarizer/aggregation/mapreduce.py`.

Written from the source only. Same caveat as the other file: outputs had been
seen before drafting, so the cold-start condition was broken.

1. The module implements map-reduce aggregation for long documents: the map
   stage summarizes each chunk independently, the reduce stage combines them.
2. The reduce stage uses a dedicated reduce profile (`__reduce__`), distinct
   from the request profile used in the map stage.
3. `run_map` supports bounded concurrency via a thread pool, plus cancellation,
   reuse of cached results, and an `on_result` callback.
4. Map results are assembled in chunk order regardless of completion order, so
   the reduce stage is deterministic.
5. On failure, the lowest-index failing chunk's error is raised and pending
   work is cancelled.
6. `consolidate` reduces in rounds until the remainder fits a single final
   prompt, bounded by `MAX_REDUCE_ROUNDS`.
7. Interim summaries are wrapped in a fresh, per-round random boundary and are
   treated as untrusted data in the reduce prompt.
8. `truncate_to_tokens` is the only lossy step in aggregation and is never
   silent (the caller warns); it verifies the result against the estimator.
9. `plan_reduce_groups` is a pure function that packs summaries into groups
   that fit a token budget.
10. `Chunk` is a frozen dataclass whose `token_estimate` and `char_count` are
    fields; chunk splitting is `split_document` in `chunking/splitter.py`, not
    a method on `Chunk`.
11. `REDUCE_SUMMARY_OVERHEAD_TOKENS` is 24 and `MAX_REDUCE_ROUNDS` is 8.
12. The module never concatenates interim summaries into the final answer; they
    are re-combined by the model under a distinct instruction set.
