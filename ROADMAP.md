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

# Backend Portability + Evaluation + Public Repo Guard (v0.5)

- Backends are adapters behind the generic `Engine` interface (`generate`, `stream`, `health`, `list_models`, `capabilities`). A declarative catalogue in `engine/backends.py` describes each local runtime; adding an OpenAI-compatible runtime is a catalogue entry, and adding a runtime with unusual behaviour is one small adapter class. Ollama is one adapter (native discovery plus the generic chat path); the generic OpenAI-compatible adapter serves `openai-compatible`/`openai`, `llama.cpp`, and `lmstudio` with documented loopback defaults.
- The pipeline never contains backend-name conditionals. It negotiates capability (`supports_streaming`, `supports_structured_output`, `supports_model_listing`, `supports_reasoning_control`, `context_length` + provenance). Streaming falls back to a single request when the engine reports no streaming support.
- Reasoning is a generic engine-level control (`reasoning_effort`, with `reasoning` accepted as an alias) translated by each adapter. Ordinary summaries default to `none`; a requested budget is omitted with a warning when the backend does not advertise support. Every request is bounded by `max_tokens`.
- Model/configuration diagnostics: `summarize doctor` reports configuration layers, reachability, the configured model, installed models, context provenance, and negotiated capabilities; `summarize models` lists what the local backend reports. Nothing is downloaded and no model is selected automatically.
- Configuration precedence is explicit and tested: CLI → environment → project config → user config → built-in defaults. Project config is opt-in (`SUMMARIZER_PROJECT_CONFIG`), so a cloned repository cannot silently force a model or endpoint. `--dry-run` reports which layer supplied the model/endpoint/reasoning, and never prints a secret.
- A local evaluation harness (`evaluation/`, `summarize evaluate`) runs the same deterministic corpus against different local models/backends. Checks are mechanical (format/JSON validity, sentinel retention, injected-marker absence, strategy, output bounds) plus latency and estimated output tokens. No external judge model; no objective quality score is claimed. `evaluate --dry-run` needs no model and is what CI runs.
- Security hardening: the adversarial corpus covers instruction injection, fake/real boundary collisions, boundary-shaped content, malicious Markdown, hostile code comments, malicious PDF text, fake JSON, control characters, second-order map/reduce injection, and malicious trusted-context files. Boundary collisions are checked and regenerated rather than trusted to randomness. Map-stage outputs are treated as untrusted intermediate data when building reduce prompts; the reduce instructions and interim summaries are structurally separated. `--context` remains explicitly user-trusted and structurally separate from the document.
- `scripts/check_repo_safety.py` is a standard-library-only, network-free guard for credentials, `.env`, local config, absolute home paths, temporary artifacts, and wildcard binds, with an explainable allowlist. Test servers stay on `127.0.0.1`.

# Production UX, Reliability, Performance & Distribution (v0.6)

- Cancellation is first-class. A per-run `CancelToken` is flipped by the SIGINT handler and checked at stage boundaries and inside long loops; the HTTP client observes it while reading a response body and while streaming (polling the socket, so a stalled server cannot block Ctrl-C). Remaining map chunks stop, output files are written atomically (an interruption never corrupts an existing file), interrupted runs are not recorded into a session, and the exit status is the existing 130 with no traceback.
- Progress goes to stderr only and is TTY-aware: an in-place per-chunk bar interactively, a start/finish line when not a TTY, disabled by `--quiet` or a non-TTY. stdout stays composable and JSON stays valid.
- Optional bounded concurrency for independent map chunks (`[chunking] concurrency`, default 1 = the original sequential behavior, hard-capped at 8). Results are assembled in chunk order, failures are reported for the lowest failing chunk, pending work is cancelled, and the reduce stage waits for all map results. The tradeoff is documented: concurrency is not automatically faster.
- An opt-in local cache/checkpoint store (`[cache] mode = off|read|write|readwrite`) keyed by a content-addressed identity (document hash, model, backend, effective profile/prompt identity, per-chunk hashes, chunking settings, engine parameters). A partial entry is a checkpoint and resumes the completed chunks; incompatible identities are never reused. It stores only derived summaries and hashes, never document contents or source paths, is bounded by entry count/bytes, and is `0700`/`0600`.
- Prompt/profile identity is content-addressed (effective template text + schema version + language/format/context presence), so a changed user profile invalidates cached results; identities are short hashes and never expose prompt text.
- CLI introspection: `config show|path|validate`, `profiles` (with `--names`/JSON), `cache status|path|clear`, plus `completions bash|zsh|fish`. `config show` attributes each value to its configuration layer and redacts credentials.
- `--dry-run` now reports the prompt identity, concurrency, and cache mode alongside the existing per-value sources.
- `--stats` reports input size, chunks, map/reduce/generation/total durations, estimated generated tokens, and estimated throughput to stderr (and into the JSON envelope when `--format json`). Numbers only; no content.
- Broken pipes (`summarize file | head`) exit 141 with no traceback and no shutdown-time flush noise.
- Packaging: a single version source (`summarizer.__version__`, read dynamically by setuptools), console script, zero runtime dependencies, `MANIFEST.in`, and a tag-triggered release workflow that builds sdist+wheel, writes `SHA256SUMS`, and publishes a GitHub release. No self-update; the running tool downloads nothing.

## Next: ship, then measure quality

The infrastructure is ahead of the evidence. The priority order is now:

1. **Ship v0.6**: publish to PyPI, install with `pipx`, get a handful of real
   users. Until people use it, more features are guesses.
2. **Build one honest quality benchmark**: a handful of documents with
   human-written reference claims, scored for factual coverage and omission,
   run against two local models. Design and caveats:
   [docs/quality.md](docs/quality.md). This is the only planned work that
   addresses whether the summaries are actually good.
3. Let those results, not a feature list, decide what comes next.

More infrastructure — refine-based aggregation, hierarchical reduction, new
profiles — is not the next step. The core function has never been evaluated,
and that gap is worth more than another milestone.

## Deferred

GUI, URL fetching, cloud models, agents, tool execution, embeddings/vector databases, self-update, background services, automatic model installation, multi-model ensemble/routing, and refine-based aggregation. These remain out of scope until a concrete need appears.