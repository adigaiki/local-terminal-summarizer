# Long-Document Handling (v0.2)

- Long documents are split at paragraph, sentence, word, then hard boundaries, with optional overlap.
- Every request is budgeted against the *discovered* context window from the local server (`/api/show` / `/api/tags`), the configured `context_length`, or a documented fallback — never a hardcoded per-model value.
- Map-reduce is the v0.2 long-document strategy; interim summaries are untrusted data, re-scoped per round.
- Strict mode refuses oversized input instead of silently degrading to map-reduce.
- Resource limits bound input bytes, lines, and chunk count so an effectively infinite stdin stream cannot be read into memory.
- Provenance (source, boundary, offsets) is preserved per chunk and included in JSON output.