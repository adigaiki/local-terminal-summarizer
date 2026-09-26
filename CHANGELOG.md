# Changelog

Notable changes per release. The project is pre-1.0 and not yet on PyPI; until
it is, "released" means a git tag. Design notes and rationale live in
[ROADMAP.md](ROADMAP.md).

## Unreleased

- **Structured PDF tables** (`[input] structure_tables`, on by default):
  simple extracted tables are rewritten so every value carries its own label,
  removing the column-mapping step that small models get wrong.
- **`--verify`**: mechanically flag summary numbers and `ACRONYM (expansion)`
  phrases that do not occur in the source. Grep, not a judge model.
- **`fonttools` added to the `pdf`/`ocr` extras**; without it pypdf logs a
  warning per CFF/Type1 font and floods stderr on real papers.
- **Documentation reorganized**: the long README was split into
  [docs/](docs/index.md), and [docs/quality.md](docs/quality.md) now records
  the first honest quality benchmark (claims, results, caveats).
- Fixed a flaky CI failure in the response-cap loopback test on Python 3.11
  (the test server now drains the request body and sends `Content-Length`).

## v0.6.0 — 2026-09-21

Production UX, reliability, performance, and distribution: first-class
cancellation (SIGINT, exit 130, atomic output), TTY-aware progress on stderr,
optional bounded concurrency, an opt-in local cache/checkpoint store, CLI
introspection (`config`, `profiles`, `cache`, `completions`), `--stats`,
broken-pipe handling (exit 141), and a tag-triggered release workflow. Zero
runtime dependencies.

## v0.5.0 — 2026-09-21

Backend portability, evaluation, and the public-repo guard: backends became
adapters behind one `Engine` interface with capability negotiation; Ollama plus
a generic OpenAI-compatible adapter (`openai-compatible`, `llama.cpp`,
`lmstudio`); `summarize doctor`/`models`; explicit configuration precedence with
opt-in project config; a local evaluation harness; and
`scripts/check_repo_safety.py`.

## v0.4.0 — 2026-09-18

Named sessions: directories of plain files under the sessions root, explicitly
selected, with atomic digests rendered from `state.json` and `flock`
serialization. No database, daemon, or shell hooks.

## v0.3.0 — 2026-09-17

Rich readers and provenance: stdin, text, Markdown, source code, and optional
PDF all produce one `Document`; line/page provenance; `--format json`; and
optional, local-only PDF/OCR with resource limits.

## v0.2.0 — 2026-09-17

Long-document handling: boundary-aware chunking with overlap, context
discovery from the local server, map-reduce aggregation with untrusted interim
summaries, `--strict`, and bounded input.
