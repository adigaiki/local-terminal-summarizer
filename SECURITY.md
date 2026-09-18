# Security Policy / Threat Model

## Trust boundaries

Document content is untrusted. For every request it is wrapped in a fresh, cryptographically random boundary (`secrets.token_hex(12)`), separate from trusted instructions; map-reduce interim summaries get the same treatment with per-round boundaries, so instruction-like text in a document or an interim summary is treated as data, not as commands. `--context FILE` is the only user-supplied *trusted* extra input and gets its own labelled section.

## Readers and untrusted content

Every reader — stdin, text, Markdown, source code, and PDF — produces one `Document` whose content is untrusted data, wrapped in a fresh random per-request boundary. Reader metadata (filenames, Markdown headings, code comments, PDF page text, extraction warnings) is never treated as instructions and never enters the trusted prompt sections; tests assert this for TXT, Markdown, source code, and PDF text, and for a hostile PDF filename.

Readers do not execute, fetch, or render anything: Markdown is never converted to HTML, link destinations are never retrieved, source code is never parsed or run, project configuration is never interpreted as code, and PDF metadata is never sent anywhere. Content containing NUL bytes is rejected as binary rather than summarized.

## Local-only core

There is no cloud API, telemetry, analytics, or update channel in normal operation. The engine endpoint defaults to `http://localhost:11434`; configure your own local endpoint/model. `--check-update` is the only opt-in network operation and requires you to set a URL yourself; without it the tool performs no update traffic.

PDF extraction runs locally through pypdf (BSD-3-Clause). OCR runs only local binaries (`tesseract`, Poppler's `pdftoppm`/`pdfinfo`) and optional local Python packages: no OCR service is contacted, and nothing is installed automatically. Optional extras are diagnosed by `summarize doctor` without being reported as failures.

## Resource limits

Readers are bounded so a hostile document cannot exhaust memory: `input.max_bytes`, `input.max_lines`, `input.max_pdf_pages` (page count, checked before extraction) and `input.max_extracted_bytes` (checked while accumulating page text, not afterwards). Known non-PDF inputs are only summarized when the content is text; unknown text extensions remain usable. Limits are configuration, never hardcoded per-model, and exceeding one fails with a clear, actionable error.

Residual risk, stated honestly: these limits bound the *extracted text* a PDF produces. A malicious PDF could still consume memory inside the parser while decompressing content streams, before any text is returned; Python cannot safely impose a memory ceiling on itself from inside the process. Bound such input by size (`input.max_bytes`), page count, and your own process limits (for example `systemd-run --scope -p MemoryMax=1G` or `ulimit -v`).

## Sessions

Sessions are opt-in and local. A run is recorded only when a session is explicitly selected — `--session NAME`, `$SUMMARIZER_SESSION`, or the documented same-directory rule (see README "Which session does a run record into?") — and nothing is recorded otherwise, so unrelated terminals cannot inherit a session by accident. There is no daemon, no filesystem watcher, and no process tracking; no shell hook is ever installed automatically.

Session files live under the sessions root (`[session] notes_dir`) as directories per session with restrictive permissions (0700 directories, 0600 files) and contain pointers only: source path, profile, timestamps, document type, page/chunk counts, strategy, duration, and a one-sentence extract capped at 200 characters. Full summaries, prompts, document text, environment variables, and credentials are never written to session files or logs.

Because session data reveals which files were summarized and when, keep the sessions root outside synced or shared locations if that matters to you. Sessions older than `[session] max_age_hours` are flagged by `summarize session status` and are never closed or deleted automatically. Updates are serialized with `flock` and written through atomic replace, so Ctrl-C and concurrent runs cannot corrupt state.

## Secrets & configuration

Keep configuration outside Git (`~/.config/summarizer/config.toml`, `./summarizer.toml`); `config.example.toml` is the only committed example and contains placeholders only. Never commit API keys or tokens; the project ships none. Machine-specific paths, personal prompt overrides (`/prompts/`), logs, caches, virtualenvs, and editor state are gitignored.

## Reporting

For vulnerabilities, open a private security advisory (GitHub "Security" tab) rather than a public issue.

## Known limitations (v0.3)

- Prompt-injection resistance depends on the model honouring the boundary contract; the boundary raises the bar but cannot make a probabilistic system a guarantee.
- Token counts are estimates, not a tokenizer; chunk sizes are approximate.
- PDF/OCR readers are optional extras with their own supply-chain surface. PDF text extraction depends on pypdf's heuristics and can differ from the visual layout; page counts and page starts reflect extracted text, not rendered pagination.
- Encoding detection is best-effort: the requested encoding is tried strictly first, then UTF-8 with replacement characters. Fallback decoding is recorded as a warning rather than silently accepted, but it cannot always recover the original bytes.
- OCR accuracy depends on the local Tesseract language data and scan quality.
