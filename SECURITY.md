# Security Policy / Threat Model

## Trust boundaries

Document content is untrusted. For every request it is wrapped in a fresh, cryptographically random boundary (`secrets.token_hex(12)`), separate from trusted instructions; map-reduce interim summaries get the same treatment with per-round boundaries, so instruction-like text in a document or an interim summary is treated as data, not as commands.

Boundary collisions are handled explicitly, not left to probability. Before wrapping content the tool checks whether the exact boundary token occurs in every piece of content it will enclose (the document and any trusted context file) and regenerates the token on a collision, bounded by a small retry limit; if no safe token can be produced it refuses to build the prompt rather than emit a forgeable one. Boundary-shaped text in the document therefore cannot be used to forge the delimiter, and the document on disk is never modified.

### What the boundary does and does not buy

Collision checking is **defence-in-depth, not prevention**. It removes the ability to forge the delimiter; it does not stop persuasive text *inside* the region from influencing the model. A model may still follow instructions embedded in a document, and the prompt contract is probabilistic.

The blast radius is bounded by what the tool does with model output: it prints it (or emits it as JSON) and nothing else. There is no tool execution, no network request, no file write, and no shell driven by model output. A successful injection therefore produces a wrong or misleading summary — a real problem for trust, but not code execution. The one way to make it worse is to feed `--format json` into something automated without validating it; if you do that, treat the summary text as untrusted input at that boundary too.

### Map/reduce is a two-stage boundary, not a trust promotion

Interim summaries produced by the map stage are treated as **untrusted intermediate data**. The reduce prompt is built with its own fresh per-round boundary and places the interim summaries in a dedicated untrusted section; the reduce instructions are a separate, structurally distinct trusted section. The tool does not rely on the fact that the intermediate text came from its own model, so a poisoned document can inject at the map stage and still cannot escape the reduce boundary. This is an explicit invariant with regression tests.

### `--context` is trusted by you, and only you

`--context FILE` is the one user-supplied input marked trusted: it is placed in its own labelled `USER-SUPPLIED CONTEXT (TRUSTED)` section and is never mixed with the document. Only pass a context file whose origin you trust, because its content is treated as instructions. Even then it is boundary-checked, so a context file cannot forge the document boundary, and document content never appears in the trusted context section.

## Readers and untrusted content

Every reader — stdin, text, Markdown, source code, and PDF — produces one `Document` whose content is untrusted data, wrapped in a fresh random per-request boundary. Reader metadata (filenames, Markdown headings, code comments, PDF page text, extraction warnings) is never treated as instructions and never enters the trusted prompt sections; tests assert this for TXT, Markdown, source code, and PDF text, and for a hostile PDF filename. An adversarial corpus exercises instruction injection, fake closing boundaries, boundary-shaped content, malicious Markdown, hostile source-code comments, malicious PDF text, fake JSON, control characters, and second-order map/reduce injection as structural, not model-behavioural, tests.

Readers do not execute, fetch, or render anything: Markdown is never converted to HTML, link destinations are never retrieved, source code is never parsed or run, project configuration is never interpreted as code, and PDF metadata is never sent anywhere. Content containing NUL bytes is rejected as binary rather than summarized.

## Local-only core

There is no cloud API, telemetry, analytics, or update channel in normal operation; everything stays on loopback. The backend endpoint defaults to `http://localhost:11434`; configure your own local endpoint/model. `--check-update` is the only opt-in network operation and requires you to set a URL yourself; without it the tool performs no update traffic.

Backends are local adapters (Ollama, generic OpenAI-compatible, llama.cpp server, LM Studio). No cloud provider is implemented or configured. `summarize models`, `summarize doctor`, and `summarize evaluate` contact only the configured local endpoint; `evaluate --dry-run` contacts nothing. No models are downloaded, no models are installed, and no model is selected automatically.

PDF extraction runs locally through pypdf (BSD-3-Clause). OCR runs only local binaries (`tesseract`, Poppler's `pdftoppm`/`pdfinfo`) and optional local Python packages: no OCR service is contacted, and nothing is installed automatically. Optional extras are diagnosed by `summarize doctor` without being reported as failures.

## Resource limits

Readers are bounded so a hostile document cannot exhaust memory: `input.max_bytes`, `input.max_lines`, `input.max_pdf_pages` (page count, checked before extraction) and `input.max_extracted_bytes` (checked while accumulating page text, not afterwards). Known non-PDF inputs are only summarized when the content is text; unknown text extensions remain usable. Limits are configuration, never hardcoded per-model, and exceeding one fails with a clear, actionable error.

Residual risk, stated honestly: these limits bound the *extracted text* a PDF produces. A malicious PDF could still consume memory inside the parser while decompressing content streams, before any text is returned; Python cannot safely impose a memory ceiling on itself from inside the process. Bound such input by size (`input.max_bytes`), page count, and your own process limits (for example `systemd-run --scope -p MemoryMax=1G` or `ulimit -v`).

## Sessions

Sessions are opt-in and local. A run is recorded only when a session is explicitly selected — `--session NAME`, `$SUMMARIZER_SESSION`, or the documented same-directory rule (see [docs/sessions.md](docs/sessions.md)) — and nothing is recorded otherwise, so unrelated terminals cannot inherit a session by accident. There is no daemon, no filesystem watcher, and no process tracking; no shell hook is ever installed automatically.

Session files live under the sessions root (`[session] notes_dir`) as directories per session with restrictive permissions (0700 directories, 0600 files) and contain pointers only: source path, profile, timestamps, document type, page/chunk counts, strategy, duration, and a one-sentence extract capped at 200 characters. Full summaries, prompts, document text, environment variables, and credentials are never written to session files or logs.

Because session data reveals which files were summarized and when, keep the sessions root outside synced or shared locations if that matters to you. Sessions older than `[session] max_age_hours` are flagged by `summarize session status` and are never closed or deleted automatically. Updates are serialized with `flock` and written through atomic replace, so Ctrl-C and concurrent runs cannot corrupt state.

## Secrets, configuration, and the public repository

Keep configuration outside Git (`~/.config/summarizer/config.toml`, `./summarizer.toml`); `config.example.toml` is the only committed example and contains placeholders only. Never commit API keys or tokens; the project ships none. Machine-specific paths, personal prompt overrides (`/prompts/`), logs, caches, virtualenvs, and editor state are gitignored.

`scripts/check_repo_safety.py` is a small, reviewable, standard-library-only guard that scans the files git would include for credentials (private keys, cloud/AI API keys, bearer tokens, credential assignments), `.env` files, local `summarizer.toml`, absolute home-directory paths, temporary artifacts, and wildcard bind addresses. It performs no network access; findings are `path:line: rule` with a redacted preview, and intentional false positives are allowlisted with a reason in `scripts/leak_allowlist.txt`. Project config is opt-in (`SUMMARIZER_PROJECT_CONFIG`), so a cloned repository cannot silently change your model or endpoint. CI runs the guard without any model, GPU, or network beyond dependency installation.

## Local cache, checkpoints, and privacy

The optional cache/checkpoint store (`[cache]`, off by default) is the only new
persistent state in v0.6. Its privacy model is deliberately narrow:

- **It never stores the document.** Entries contain only derived summaries
  (model output) and hashes: a document content hash, per-chunk content
  hashes, the model/backend name, the profile name and a content-addressed
  prompt identity, chunking numbers, and relevant engine parameters.
- **It never stores source paths.** A run is matched by document hash, not by
  filename, so the cache does not reveal which files you summarized.
- **It is self-invalidating.** A change to the document, model, profile/prompt
  text, chunk plan, output format, language, or relevant engine settings
  changes the key; incompatible entries are never reused (verified against the
  stored identity as defence in depth).
- **It is bounded and private.** Directories are `0700` and files `0600`;
  `max_entries`/`max_bytes` prune oldest-first and `max_chunk_bytes` skips an
  oversized result. `summarize cache clear` removes everything and `summarize
  cache path` shows where it lives.
- **It is opt-in.** With `mode = "off"` (the default) no directory is created.

Derived summaries can still be revealing, so treat the cache like the session
root: keep it outside synced or shared locations if that matters. Because
matching is by content hash, an entry for a document you later edit is simply
not found rather than reused.

Progress rendering writes only to stderr, holds counters and timings (no
content), and is disabled by `--quiet` or a non-TTY. `--stats` emits numbers
only and never document or model text. Cancellation is cooperative: Ctrl-C
stops in-flight reads/streams, stops remaining map chunks, and never leaves a
partially written output file (writes are atomic).

## Reporting

For vulnerabilities, open a private security advisory (GitHub "Security" tab) rather than a public issue.

## Known limitations (v0.6)

- Prompt-injection resistance is **not solved and is not claimed to be solved**. The boundary, collision hardening, and structural separation raise the bar and are regression-tested as *structural* guarantees; whether a model honours them remains probabilistic. A model can still summarize maliciously, hallucinate, or quote hostile text in its output. The adversarial corpus treats an echoed injected token as a signal for review, not proof of failure.
- The evaluation harness uses mechanical checks (validity, format, sentinel retention, marker absence, strategy, bounds). It is not an objective quality score and uses no external judge model.
- Token counts, latency, and throughput are estimates. Backend-reported token usage is best-effort and not comparable across backends.
- Concurrency is cooperative and bounded (max 8). A single local model may serialize requests anyway; concurrency is not guaranteed to improve throughput and can increase load.
- The cache/checkpoint store keys on a document *content hash*. Editing a document produces a new key rather than a reused entry, so identical content presented with different encodings or whitespace may not hit. Partial checkpoints are only as good as the last completed chunk.
- Token counts are estimates, not a tokenizer; chunk sizes are approximate.
- PDF/OCR readers are optional extras with their own supply-chain surface. PDF text extraction depends on pypdf's heuristics and can differ from the visual layout; page counts and page starts reflect extracted text, not rendered pagination.
- Encoding detection is best-effort: the requested encoding is tried strictly first, then UTF-8 with replacement characters. Fallback decoding is recorded as a warning rather than silently accepted, but it cannot always recover the original bytes.
- OCR accuracy depends on the local Tesseract language data and scan quality.
- The repository safety guard is deliberately heuristic. Known token formats are detected; an unusual or novel credential shape may not match, and a false positive must be allowlisted with a stated reason.
