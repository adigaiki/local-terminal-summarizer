# summarizer

`summarize` is a Linux-first terminal program for summarizing files and
standard input through a local LLM server. Its normal summarization path makes
no internet requests and has no runtime dependencies beyond Python 3.11+.

The default backend is [Ollama](https://ollama.com/) at
`http://localhost:11434`, using `llama3.1:8b`. Backends are pluggable
adapters: Ollama (with native model/context discovery), a generic
OpenAI-compatible adapter, and named local runtimes that speak that API
(`llama.cpp` server, LM Studio). See "Backends and capability negotiation".

## Install and run

With [pipx](https://pipx.pypa.io/) (recommended for a CLI tool; isolates the
package and puts `summarize` on your `PATH`):

```sh
pipx install .
```

Or from a checkout into a virtualenv:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
ollama pull llama3.1:8b
printf 'Mercury is the closest planet to the Sun.' | .venv/bin/summarize
```

Optional extras: `summarizer[pdf]` (PDF text) and `summarizer[ocr]` (local
Tesseract/Poppler OCR). The base install has no runtime dependencies.

Once installed, typical use is:

```sh
summarize article.md
git diff | summarize --profile code
summarize report.pdf --profile academic
summarize notes.txt --format json | jq
summarize notes.txt -o summary.md
summarize notes.txt --dry-run
summarize doctor
summarize models
summarize evaluate --model qwen3:8b
```

`summarize -` explicitly reads stdin. When stdin is a TTY, running
`summarize` with no input reports an error instead of waiting forever.

## Configuration

Configuration is layered, with later sources taking precedence:

1. command-line options
2. `SUMMARIZER_*` environment variables
3. `./summarizer.toml` (project config — **opt-in**, see below)
4. `$XDG_CONFIG_HOME/summarizer/config.toml` (or `~/.config/summarizer/config.toml`)
5. built-in defaults

Project config is opt-in and predictable, so a cloned repository cannot
silently force a surprising model, endpoint or profile on you. `./summarizer.toml`
is read only when you ask for it:

```sh
SUMMARIZER_PROJECT_CONFIG=1 summarize article.md        # read ./summarizer.toml
SUMMARIZER_PROJECT_CONFIG=./team.toml summarize a.md    # read an explicit file
```

Unset, no project file is read. When it is read it keeps its place in the
precedence chain above. `--dry-run` and `summarize doctor` show which layer
supplied each engine setting and never print a secret.

Example `summarizer.toml`:

```toml
[engine]
backend = "ollama"              # ollama, openai-compatible, llama.cpp, lmstudio
endpoint = "http://localhost:11434"
model = "llama3.1:8b"
timeout_seconds = 60
retries = 1
# Set this when the backend cannot report its context length.
context_length = 0
max_tokens = 1024              # hard cap per map/reduce generation
reasoning_effort = "none"      # none, low, medium, or high when supported
# `reasoning` is accepted as a generic alias for `reasoning_effort`.
# reasoning = "none"

[defaults]
profile = "plain"
output_format = "markdown"
stream = true
chunk_strategy = "auto"         # auto, tokens, or chars

[chunking]
max_tokens_per_chunk = 3000
overlap_tokens = 200
reserve_output_tokens = 1000

[input]
max_bytes = 52428800
max_lines = 1000000
encoding = "utf-8"
```

Environment overrides include `SUMMARIZER_BACKEND`, `SUMMARIZER_ENDPOINT`,
`SUMMARIZER_MODEL`, `SUMMARIZER_TIMEOUT`, `SUMMARIZER_RETRIES`,
`SUMMARIZER_MAX_TOKENS`, `SUMMARIZER_MAX_RESPONSE_BYTES`,
`SUMMARIZER_REASONING_EFFORT` (alias `SUMMARIZER_REASONING`),
`SUMMARIZER_PROFILE`, and `SUMMARIZER_FORMAT`. `SUMMARIZER_PROJECT_CONFIG`
opts into project config and `SUMMARIZER_CONFIG` overrides the user config
path.

`reasoning_effort` defaults to `none`, appropriate for ordinary summaries.
It is sent only to backends that advertise support for compatible reasoning
control; a requested value is omitted (with a verbose warning) when the
backend does not advertise it, rather than risking an incompatible request.
Every request sends `max_tokens`, so generation is bounded even when a model
has a large context window. Responses are bounded too: accumulation is capped
at `max_response_bytes` (default 32 MiB; 0 disables) per request, so a
misbehaving local server cannot grow memory without bound — an oversized
reply fails cleanly with exit code 2 instead.

Built-in profiles are `plain`, `code`, `academic`, and `meeting`. Put a
`NAME.md` profile in `~/.config/summarizer/prompts/` to override or add one.
Profiles must contain exactly one of `{{input}}` or `{{summaries}}`; optional
placeholders are `{{context}}`, `{{lang}}`, and `{{output_format}}`.

## Backends and capability negotiation

Backends are adapters behind one generic interface — `generate()`, `stream()`,
`health()`, `list_models()`, and `capabilities()`. The pipeline never branches
on a backend name; it asks the engine what it supports and negotiates:

| Capability | Meaning |
| --- | --- |
| `supports_streaming` | live token streaming is available |
| `supports_structured_output` | JSON response format is advertised (three-valued: yes/no/unknown) |
| `supports_model_listing` | the endpoint can enumerate installed models |
| `supports_reasoning_control` | the endpoint accepts a reasoning budget |
| `context_length` (+ source) | the window to budget against, and how it was learned |

Implemented adapters:

| `backend` | Kind | Notes |
| --- | --- | --- |
| `ollama` | native + OpenAI-compatible | discovery via `/api/ps`, `/api/show`, `/api/tags` |
| `openai-compatible` (alias `openai`) | generic | any local OpenAI Chat Completions server |
| `llama.cpp` (alias `llama-cpp`) | generic | default endpoint `http://localhost:8080` |
| `lmstudio` (alias `lm-studio`) | generic | default endpoint `http://localhost:1234` |

Backends with no declared endpoint keep the configured one. If you never set
an endpoint, selecting a backend uses its documented loopback default. Adding
an OpenAI-compatible runtime is a catalogue entry; adding a runtime with
unusual behaviour is one small adapter class. There are no cloud providers.

## Model and configuration discovery

```sh
summarize doctor              # layers, reachability, model, context, capabilities
summarize models              # models the configured local backend reports
summarize models --format json
```

`doctor` shows where each engine setting came from, whether the configured
model exists, which models are installed (when the backend can enumerate them
without downloading anything), the context window and its provenance, and the
negotiated capabilities. `models` never downloads and never selects a model
for you: if the configured model is absent you get the installed list and an
actionable hint. Nothing here contacts an external service.

## Evaluation

A small deterministic harness runs the same corpus against different local
models/backends:

```sh
summarize evaluate --dry-run                 # list cases, contact nothing
summarize evaluate --model qwen3:8b          # run everything
summarize evaluate --model qwen3:8b --tags core,code,security
summarize evaluate --model qwen3:8b --format json
```

```text
evaluation/
  fixtures/     documents used by cases
  cases/        case definitions (*.toml)
  runner.py     re-export of summarizer.evaluation.runner
  report.py     re-export of summarizer.evaluation.report
```

The checks are mechanical and structural: valid output format, valid JSON,
retention of a sentinel, absence of an echoed injection marker, expected
aggregation strategy, and bounded output size, plus latency and estimated
output tokens as observations. **They are not objective quality scores and no
external judge model is used.** A failing check is a prompt to look at the
model, not a verdict. `--dry-run` is what CI uses, so the framework needs no
model at all. Evaluation is separate from normal summarization.

The first case may cold-load the model; raise `--timeout` if it times out
(for example `--timeout 240`). Cases run with `stream = false` so that latency
and output size are measured rather than printed.

The corpus lives in the repository's `evaluation/` directory. When the package
is installed on its own (for example with `pipx`), point the harness at a
checkout with `--eval-dir PATH` or `$SUMMARIZER_EVAL_DIR`; the installed command
reports a clear error if it cannot find a corpus.

## Repository safety

`scripts/check_repo_safety.py` is a small, reviewable, standard-library-only
check that makes it hard to commit API keys, tokens, passwords, cookies,
private keys, `.env` files, local `summarizer.toml`, absolute `/home/...`
paths, temporary artifacts, or a wildcard bind address. It scans the files
git would include, does **not** use the network, and prints an explainable
`path:line: rule` for each finding. False positives are allowlisted with a
reason in `scripts/leak_allowlist.txt`. Run it yourself:

```sh
python scripts/check_repo_safety.py
```

## Progress, cancellation, and concurrency

Progress is written to **stderr** only, so stdout stays composable (a pipe to
`jq` or `head` is unaffected). On an interactive terminal it draws a single
updating bar (one line per chunk, never one per token); on a non-TTY it prints
a start line and a completion line. `--quiet` disables it, as does
`[defaults] progress = false`; `--no-progress` disables it for one run. JSON on
stdout remains valid regardless.

```text
Summarizing paper.pdf
[██████────] 6/10 chunks · qwen3:8b · 31s
```

Ctrl-C is a first-class cancellation. The signal cancels the current run, the
engine observes it while reading or streaming, remaining map chunks are
stopped, and the program exits with status 130 without a traceback. Output
files are written through an atomic temporary file, so an interruption never
corrupts an existing file, and an interrupted run is not recorded into a
session. Streaming requests are interrupted promptly; a non-streaming request
that has already been sent is bounded by `--timeout`, and cancellation is
observed as soon as the response body yields (or the request times out).

Independent map chunks can run with bounded concurrency (default `1`, which is
exactly the original sequential behavior):

```toml
[chunking]
concurrency = 1   # 1..8; higher is not automatically faster
```

Results are always assembled in chunk order, so provenance and the reduce stage
are deterministic. The reduce stage never starts until all required map results
are available. Whether concurrency helps depends on the backend: a single
local model often serializes requests anyway, so measure before raising it.

## Caching and resumable runs

A long map-reduce run can be checkpointed so a failure does not discard the
completed map work. Caching is **off by default** and never stores document
contents or source paths:

```toml
[cache]
mode = "off"        # off | read | write | readwrite
dir = "~/.cache/summarizer"
max_entries = 500
max_bytes = 268435456
```

Every entry is keyed by a content-addressed identity: document hash, model,
backend, the effective profile/prompt identity, the chunk plan (per-chunk
content hashes), chunking settings, and the relevant engine parameters. Change
any of those — the document, the model, the profile text, the chunking, the
output format or language — and the old entry is simply not reused. A partial
entry is a checkpoint: re-running the same command resumes the completed
chunks and fills in the rest. Entries are self-invalidating; a resumed run
refuses to reuse an incompatible one.

```sh
summarize cache status      # mode, path, entry count, size
summarize cache path
summarize cache clear
```

Cache directories are `0700` and files `0600`. Only derived summaries and
hashes are stored; `summarize cache clear` removes everything. See `SECURITY.md`
for the privacy model.

## Execution statistics

`--stats` prints numbers to stderr (and adds a `stats` object to `--format
json` output):

```text
Stats:
  input:  184320 bytes (~46080 tokens, 184290 chars)
  chunks: 12 (concurrency 1)
  reused: 6 cached chunk(s)
  map: 42.100s
  reduce: 6.400s
  total: 49.200s
  generated: ~2400 tokens (estimated)
  throughput: ~49.6 tok/s (estimate)
```

Statistics never include document or model text. Token counts and throughput
are estimates, and backend-reported numbers are not comparable across backends.

## CLI commands

| Command | Purpose |
| --- | --- |
| `summarize FILE` / `-` | summarize a file or stdin |
| `summarize doctor` | installation, configuration, engine, model, capability, and cache diagnostics |
| `summarize models` | models the configured local backend reports |
| `summarize profiles` | installed profiles, their source, and prompt identity (`--names`, `--format json`) |
| `summarize config show` | resolved configuration with sources; secrets redacted |
| `summarize config path` | config, prompt, session, and cache locations |
| `summarize config validate` | validate configuration and print the resolved engine |
| `summarize cache status\|path\|clear` | inspect and clear the optional cache |
| `summarize evaluate` | local model/backend evaluation harness |
| `summarize session start\|end\|status` | named run sessions |
| `summarize completions bash\|zsh\|fish` | shell completion script |

## Shell completions

```sh
summarize completions bash > ~/.local/share/bash-completion/completions/summarize
summarize completions zsh  > "${fpath[1]}/_summarize"
summarize completions fish > ~/.config/fish/completions/summarize.fish
```

Completions cover commands, options, formats, chunk strategies, backends, and
profiles (resolved dynamically via `summarize profiles --names`). No completion
framework is required.

## Installation and releases

The package is a normal PEP 621 project with a `summarize` console script and
no runtime dependencies; the version comes from a single source
(`summarizer.__version__`) and `summarize --version` matches it. `pipx install .`
(or `pip install .`) is sufficient.

Tagged releases are built by `.github/workflows/release.yml`: it produces a
source distribution and a wheel, writes `SHA256SUMS`, and attaches them to the
GitHub release. There is **no self-update mechanism** and the running tool
never downloads anything; upgrades are explicit (`pipx upgrade summarizer`).
The build job needs no Ollama, GPU, or model.

## Sessions

Sessions turn repeated runs into a coherent local record. Plain files only --
no database, no daemon, no background process:

```text
~/.local/share/summarizer/sessions/<name>/
  state.json    machine-readable state and run pointers (the source of truth)
  digest.md     human-readable digest, rendered from state
  digest.json   machine-readable digest
  lock          advisory flock file
```

```sh
summarize session start research       # create/activate; prints how to select it
summarize paper.pdf --profile academic
summarize session status               # sessions, age, run counts, stale warnings
summarize session end                  # finalize the digest and close
```

### Which session does a run record into?

Explicitly and deterministically, in this order:

1. `--no-session` -- never record (overrides everything);
2. `--session NAME` -- that named session;
3. `$SUMMARIZER_SESSION` -- the session named in the environment (shell-scoped);
4. otherwise, the single active session whose recorded working directory
   matches the current one;
5. otherwise, nothing is recorded.

Unrelated terminals cannot inherit a session by accident: they differ in
working directory, or they simply do not export the variable. If two active
sessions match one directory, the choice would be a guess, so nothing is
recorded and a warning says to use `--session NAME`. An explicit selection
that cannot be honoured (`--session ghost`) fails before the run, never after
wasting a model call. There is no PID tracking and no process monitoring.

### What is stored

Run entries are pointers, not content: timestamp, source, profile, document
type, PDF page count / chunk count where relevant, strategy, output format,
duration, and a one-sentence extract capped at 200 characters. Full model
output, prompt text and document text are never written to session files.
Session data is local but revealing -- it records *which files you summarized
and when* -- so keep the sessions root outside synced or shared locations if
that matters to you. Directories are created `0700` and files `0600`.

`summarize session status --format json` emits a stable
`summarizer.session.status.v1` document, separate from the summary JSON
schema. Sessions older than `[session] max_age_hours` (default 24) are
flagged in `session status` and are never closed or deleted automatically.
Updates are serialized with `flock` and written atomically, so Ctrl-C or
concurrent runs cannot corrupt state.

## Design and safety

Every reader produces the same `Document` object, including content,
provenance, MIME type, encoding, size, and reader metadata. Text, Markdown,
code, PDF, and stdin therefore share one pipeline.

Document text is untrusted data. For every model request it is put in a fresh,
cryptographically random XML-like boundary, separate from trusted security
instructions and the selected profile.

Randomness alone is not treated as sufficient: before wrapping content, the
tool checks whether the exact boundary token already appears in any content it
will enclose (document or trusted context file) and regenerates on a
collision, so literal attacker-created boundary-shaped text cannot close the
region early. If no safe token can be found it refuses to build the prompt
rather than emit a forgeable one. The user's document on disk is never
modified.

Map-reduce intermediate summaries are **untrusted intermediate data**: the
reduce prompt treats every interim summary exactly like document content,
wrapping each in its own per-round boundary. The reduce instructions are a
separate, structurally distinct trusted section; the tool does not rely on the
fact that the intermediate text was generated by its own model.

`--context FILE` is explicitly trusted by *you*: it is never mixed with the
document and receives its own labelled `USER-SUPPLIED CONTEXT (TRUSTED)`
section. Only use a context file whose origin you trust — its content is
treated as instructions, unlike the document. Context content is still
boundary-checked so it cannot forge the document boundary.

Oversized input is budgeted against the model's *discovered* context window
(via the local server's metadata when it can be read, otherwise your configured
`context_length`, otherwise a conservative fallback), then chunked at
paragraph/sentence/word boundaries with overlap and summarized via map-reduce.
Use `--chunk-strategy tokens|chars` to pick the sizing unit, and `--strict` to
refuse chunking entirely instead of degrading to map-reduce. Input bytes,
lines, and chunk count are hard-limited, and chunk provenance (source, index,
offsets, boundary) is preserved and included in `--format json`. `--dry-run`
reads and plans the request without probing or contacting an engine, and shows
which configuration layer supplied the model and endpoint.

## Configuration files and secrets

Keep your real configuration outside Git: `~/.config/summarizer/config.toml`
or `./summarizer.toml` are both gitignored, and `config.example.toml` is the
only committed example and contains placeholders only. Point `endpoint` at
your own local engine; the project ships no credentials, cloud endpoints, or
API keys. Run `python scripts/check_repo_safety.py` before pushing to catch an
accidental secret or local path; see `SECURITY.md` for the trust model and
`ROADMAP.md` for the design notes.

All diagnostics and progress are written to stderr; stdout contains only the
summary or requested JSON. `-o FILE` writes through a same-directory temporary
file and atomically replaces the destination only after a successful result.

## Readers

Every reader produces the same `Document` (content, source, MIME type,
encoding, size, and reader metadata), so the engine and chunker never know
which reader ran.

| Input | Reader | Notes |
| --- | --- | --- |
| `-` / no argument | stdin | Bounded; a TTY with no input reports an error |
| `.txt`, `.log`, other/unknown extensions | text | Any file whose content is valid text stays usable |
| `.md`, `.markdown`, `.mdown`, `.mkd` | markdown | Source is preserved verbatim; headings are collected |
| `.py`, `.rs`, `.c`, `.go`, `.toml`, ... | code | Source passes through unmodified; language is recorded |
| `.pdf` | PDF (optional) | Page-aware extraction with page provenance |

Markdown is never rendered to HTML, links are never fetched, source code is
never parsed or executed, and no reader treats document text as instructions.
Content that is binary (contains NUL bytes) is rejected with a clear error
rather than summarized as mojibake.

### Optional dependencies

```sh
pip install 'summarizer[pdf]'   # PDF text extraction (pypdf, BSD-3-Clause)
pip install 'summarizer[ocr]'   # OCR: adds pytesseract + pdf2image
```

The base install has no runtime dependencies. PDF reading is entirely local:
nothing is downloaded, no PDF metadata is sent anywhere, and encryption is
refused rather than worked around. Scanned (image-only) PDFs are detected and
reported instead of silently producing an empty summary. `--ocr` reads them
locally and additionally needs the system `tesseract` and Poppler
(`pdftoppm`, `pdfinfo`) binaries; missing components produce an actionable
error, and this tool never installs software or calls an online OCR service.
`summarize doctor` reports both capabilities, marking an intentionally
uninstalled extra with `!` rather than treating it as a failure.

### Provenance and the JSON schema

Chunks carry provenance: source, chunk index and count, character offsets,
source line range, PDF page range (when the input has pages), the boundary
that produced the cut, and the overlap size. Line and page numbers are
one-based and inclusive; character offsets are zero-based, half-open Python
string offsets. `--format json` emits a stable envelope:

```json
{
  "document": {"source": "...", "mime_type": "...", "encoding": "...",
               "size_bytes": 0, "line_count": 0, "char_count": 0,
               "metadata": {"pages": 0, "page_starts": [], "warnings": []}},
  "profile": "plain",
  "engine": {"backend": "ollama", "model": "...", "endpoint": "..."},
  "strategy": "direct|mapreduce",
  "chunks": 0,
  "summary": {},
  "warnings": [],
  "chunk_provenance": [
    {"index": 0, "count": 0, "source": "...", "boundary": "paragraph",
     "start_char": 0, "end_char": 0, "start_line": 1, "end_line": 1,
     "start_page": null, "end_page": null, "chars": 0, "est_tokens": 0,
     "overlap_chars": 0}
  ],
  "prompt_boundary": "document-...",
  "duration_seconds": 0.0
}
```

`metadata` and `chunk_provenance` are omitted when empty. Provenance never
appears in the plain/Markdown summary — only in JSON and `--dry-run` — and it
is never inserted into trusted prompt instructions.

### Resource limits

Rich formats add parsing surface, so readers are bounded by `input.max_bytes`,
`input.max_lines`, and, for PDFs, `input.max_pdf_pages` and
`input.max_extracted_bytes`. Defaults are generous for real research
documents; absurd or hostile input fails with a clear error rather than
exhausting memory.

## Exit status

| Code | Meaning |
| ---: | --- |
| 0 | success |
| 1 | input or usage error; `evaluate` with failing cases |
| 2 | local engine error |
| 3 | configuration error |
| 4 | explicit update-check error |
| 130 | interrupted with Ctrl-C |
| 141 | downstream pipe closed (e.g. `summarize file \| head`) |

`--check-update` is the only update-related operation. It is explicit,
check-only, and requires a configured update URL; normal summarization never
checks for updates or contacts the network. `summarize evaluate` returns 1
when a case's mechanical checks fail and 2 when the local engine is
unreachable; it never contacts an external service.
