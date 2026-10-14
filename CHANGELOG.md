# Changelog

Notable changes per release. The project is pre-1.0; a release is a git tag plus
an upload of that tag's artifacts to PyPI. Design notes and rationale live in
[ROADMAP.md](ROADMAP.md).

## Unreleased

- **Docs-only:** [docs/installation.md](docs/installation.md) and the README
  install section now cover installing from PyPI (`pipx install
  local-terminal-summarizer`, `uv tool install`, or a virtualenv), and the
  README carries a PyPI version badge.
- **Docs-only:** the maintainer publishing section now says to upload the
  artifacts attached to the GitHub release, verified against `SHA256SUMS`,
  rather than a local build. Builds are not byte-reproducible, so a rebuild
  would put different bytes on PyPI than the release.
- **`doctor` reports the running entry point.** The `executable:` line was
  `shutil.which("summarize")`, i.e. whatever `summarize` came first on `PATH`,
  which can be a different install than the one running (making the Python
  version and extras in the same block look wrong). It now resolves the
  running entry point, falling back to `sys.executable -m summarizer`, and adds
  a non-fatal `!` line naming both paths when a different `summarize` is first
  on `PATH`.
- **`--dry-run` says when its context window is a guess.** With no configured
  window the plan printed the fallback window and a chunk count as if it were
  final, but a dry run contacts no backend and the served window may differ
  from the fallback. The plan now carries a `note:` line saying the fallback
  window makes chunking provisional. An explicit `[engine] context_length` is
  used as before and gets no note.
- **Metadata:** `authors` now names Aditya Gaikwad instead of "summarizer
  contributors" (no email added), and `LICENSE` carries the same holder. This
  shows up on the PyPI project page from 0.7.1; nothing in the installed
  package changes.
- **Internal:** the repository safety guard flags filenames that start with a
  dash (the stray file a mistyped `cmd > --flag` leaves behind), so the next one
  is caught before it is committed. Development tool only; nothing in the
  installed package changes.

## v0.7.0 — 2026-10-13

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
- **Docs:** the README's network claim is now precise (loopback by default, with
  the opt-in exceptions named), and `--check-update` is documented there.
- **Behavior change:** a non-loopback `[engine] endpoint` is now refused
  (exit 3) unless you opt in with `[engine] allow_non_loopback = true` (or
  `SUMMARIZER_ALLOW_NON_LOOPBACK=1`); `doctor` and `--dry-run` warn instead of
  refusing.
- **Packaging:** the distribution is named `local-terminal-summarizer` (the
  import package stays `summarizer`, the command stays `summarize`), because an
  unrelated project owns `summarizer` on PyPI. Not published yet.
- **Streaming timeouts:** streaming waits for the first byte up to
  `[engine] first_token_timeout_seconds` (default 600; prompt processing on a
  CPU-bound model can be slow), then applies `timeout_seconds` as an idle
  timeout reset on every byte. Non-streaming keeps the total `timeout_seconds`
  deadline. The stream transport no longer polls with socket read timeouts
  (which poisoned the socket on the first stall).
- **Platform support:** CI now runs the full suite on macOS
  (`macos-latest`) as well as Linux; the README platform note says so.
- **Context window:** a run warms an unloaded Ollama model once and re-reads
  the *served* window from `/api/ps` before planning, instead of trusting the
  trained maximum from `/api/show` (which can be far larger, and led Ollama to
  silently drop the beginning of a document). If the warm-up fails it plans
  against a conservative cap and warns. `doctor` reports the trained maximum
  with a `!` line and points at `ollama ps`.
- **Silent truncation is detected:** Ollama processes a truncated prompt as
  exactly `window/2 + 2` prompt tokens (measured on Ollama 0.35.0). When that
  signature matches and the estimate is far above the count, the run is retried
  with smaller chunks sized from the observed ratio; if it still truncates it
  warns, sets `truncated: true` in the JSON envelope, and exits 0 (or fails
  under `--strict`). A lower-confidence match only notes it on stderr, with no
  retry and no flag.
- **Docs:** the README was restructured and shortened (detail moved to
  `docs/`, including a new `docs/troubleshooting.md`). It now shows a real
  example summary, states that `doctor`'s reported window is state-dependent
  (check `ollama ps`), and compares `summarize` with `llm`, `fabric`, and
  `ollama run`.

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
