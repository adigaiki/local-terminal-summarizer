# Long-Document Handling (v0.2)

- Long documents are split at paragraph, sentence, word, then hard boundaries, with optional overlap.
- Every request is budgeted against the *discovered* context window from the local server (`/api/ps` serving window for the loaded model, then `/api/show` `/api/tags` metadata), the configured `context_length`, or a documented fallback — never a hardcoded per-model value. The serving window of a loaded model wins over the trained maximum, because requests must fit what the server actually provides.
- Map-reduce is the v0.2 long-document strategy; interim summaries are untrusted data, re-scoped per round.
- Strict mode refuses oversized input instead of silently degrading to map-reduce.
- Resource limits bound input bytes, lines, and chunk count so an effectively infinite stdin stream cannot be read into memory.
- Provenance (source, boundary, offsets) is preserved per chunk and included in JSON output.

# Rich Readers + Provenance (v0.3)

- Readers (stdin, text, Markdown, source code, PDF) all produce one `Document`; the engine and chunker stay reader-agnostic and selection is by extension, with unknown extensions still usable when the content is text.
- Markdown is preserved verbatim (never rendered to HTML, never executed, link destinations never fetched); source code passes through unmodified with comments and formatting intact; nothing is parsed or executed.
- Source code, Markdown, and text keep line-number provenance; PDFs keep page provenance as character offsets (`page_starts`), so a chunk can report its source line range and page range.
- PDF extraction is optional (pypdf, BSD-3-Clause), page-aware, and fully local. Empty pages are preserved in position, per-page failures become recorded warnings, scanned/image-only PDFs are detected and reported, and encrypted PDFs are refused. OCR remains opt-in, local-only (Tesseract + Poppler), never auto-installed, and never online.
- Rich-format resource safety: `input.max_pdf_pages` is enforced before extraction and `input.max_extracted_bytes` while accumulating page text, alongside the existing byte/line limits.
- `--format json` exposes a documented, stable provenance structure; provenance never leaks into plain/Markdown output and never enters trusted prompt instructions. `--dry-run` reports reader metadata (type, encoding, size, pages, extracted size, warnings) without contacting an engine.
- `summarize doctor` reports optional reader capabilities, marking an uninstalled extra with `!` instead of reporting a failure.

# Sessions + Workflow (v0.4)

- Named sessions are directories of plain files (`state.json` + `digest.md` + `digest.json` + lock) under the sessions root: no database, no daemon, no background behavior, no shell hooks installed automatically.
- Recording is explicit and documented: `--no-session` > `--session NAME` > `$SUMMARIZER_SESSION` > the single active session matching the run's working directory > nothing. Ambiguity is never guessed; explicit selections are validated before the run so a typo never costs a model call.
- Digests are rendered atomically *from* `state.json` (the source of truth), so an interruption cannot leave a half-written digest, and concurrent writers serialize with `flock`.
- Run entries are compact pointers (timestamp, source, profile, document type, pages/chunks, strategy, duration, one-sentence extract capped at 200 characters). Full model output, prompts, and document text are never stored.
- Stale sessions older than `[session] max_age_hours` are flagged in `session status` and never closed or deleted automatically.
- `session status --format json` emits a stable `summarizer.session.status.v1` document, separate from the summary JSON schema.
- The pipeline produces a small run event that the session manager consumes; session logic stays out of `cli.py` and the engine client, and remains independent of readers, chunking, and model logic.