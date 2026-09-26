# summarizer

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/adigaiki/local-terminal-summarizer/actions/workflows/ci.yml/badge.svg)](https://github.com/adigaiki/local-terminal-summarizer/actions/workflows/ci.yml)

`summarize` is a Linux-first command-line summarizer that turns a file or stdin
into a concise summary using a **local** LLM server you already run (Ollama,
llama.cpp, LM Studio, or any OpenAI-compatible server). It is a Unix tool:
pipe a document in, get a summary on stdout, with long-document chunking, a
trusted/untrusted input boundary, stable JSON output, and diagnostics.

Nothing is sent to the internet. The tool talks to your model server over
loopback (`http://localhost:11434` by default) and nowhere else.

- MIT-licensed, Python 3.11+.
- The core install has **zero runtime dependencies** beyond Python; PDF and
  OCR are optional extras that add their own.
- No cloud, no telemetry, no model download, no self-update.

## Why not just `ollama run model < file`?

`ollama run` prints whatever the model says, with no structure around it.
`summarize` wraps the same local model with the things a summarizer needs:
documents longer than the context window are chunked and reduced; the document
is wrapped in a fresh random boundary and treated as untrusted data while
`--context` stays trusted; `--format json` gives a stable envelope with chunk
provenance; and `summarize doctor` tells you what is reachable, configured, and
missing. It is one small tool, not a chat client.

## Table of contents

- [Requirements](#requirements)
- [Install](#install)
- [Quick start (Ollama)](#quick-start-ollama)
- [Common usage](#common-usage)
- [Supported backends](#supported-backends)
- [Supported inputs](#supported-inputs)
- [Configuration](#configuration)
- [Profiles](#profiles)
- [Safety: trusted vs. untrusted input](#safety-trusted-vs-untrusted-input)
- [Output and the JSON schema](#output-and-the-json-schema)
- [Commands](#commands)
- [Speed and system load](#speed-and-system-load)
- [Troubleshooting](#troubleshooting)
- [Documentation](#documentation)
- [Contributing, changelog, license](#contributing-changelog-license)

## Requirements

- **Python 3.11 or newer.**
- **A local model server** with at least one model pulled. The default backend
  is [Ollama](https://ollama.com/); see [Quick start](#quick-start-ollama).
- **A model that fits your machine.** A 3B–8B instruct model is plenty for
  summarization. Rule of thumb: a Q4-quantized model needs roughly its
  download size of RAM, plus a little for the context window. If the model
  fits in GPU memory it is fast (tens of tokens/second); if it spills to CPU
  it still works but can drop to ~1 token/second. `ollama ps` shows the
  CPU/GPU split and the serving context window.
- **Platforms:** developed and tested on **Linux** (the only OS classifier).
  The code uses only POSIX APIs (`flock`, signals), so **macOS and WSL are
  expected to work but are not covered by CI**. Native Windows is unsupported.

## Install

Not on PyPI yet. Install from a checkout with
[pipx](https://pipx.pypa.io/) (isolated, puts `summarize` on your `PATH`):

```sh
git clone https://github.com/adigaiki/local-terminal-summarizer.git
cd local-terminal-summarizer
pipx install .            # or: uv tool install .
```

From a virtualenv instead:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/summarize --version
```

Optional extras (the base install stays dependency-free):

```sh
pipx install '.[pdf]'     # PDF text extraction (pypdf + fonttools)
pipx install '.[ocr]'     # OCR: adds pytesseract + pdf2image; also needs the
                          # system tesseract and Poppler (pdftoppm, pdfinfo)
```

Uninstall:

```sh
pipx uninstall summarizer   # or: uv tool uninstall summarizer
```

Releases, packaging, and the published wheel are described in
[docs/installation.md](docs/installation.md).

## Quick start (Ollama)

```sh
# 1. Install Ollama (Linux) and start it: https://ollama.com/download
curl -fsSL https://ollama.com/install.sh | sh

# 2. Pull a model that fits your machine.
ollama pull qwen2.5:3b

# 3. Check the whole setup before you trust it.
summarize doctor
```

`summarize doctor` is the first thing to run when anything is wrong. It checks
configuration, whether the backend is reachable, whether the configured model
exists, the context window and where it came from, and which optional extras
are installed. A healthy run looks like this (with `qwen2.5-3b-16k`
configured):

```text
Summarizer diagnostics

✓ Configuration — loaded from config files + environment
    precedence: command line > environment > project config > user config > built-in defaults
    user config: ~/.config/summarizer/config.toml
✓ Installation — summarizer 0.6.0 on Python 3.14
✓ Cache — off (no local state is written)
✓ Backend reachable
    endpoint: http://localhost:11434
✓ Model available — configured model 'qwen2.5-3b-16k' is installed
✓ Streaming supported — yes
✓ Context window known — 16384 tokens (server)
! OCR — optional, not installed (missing: pytesseract/pdf2image)

Backend:
  backend:  ollama
  model:    qwen2.5-3b-16k
  endpoint: http://localhost:11434

Everything looks good.
```

If the backend is down, `doctor` says so plainly and exits non-zero, and a
normal run fails with a hint rather than a traceback:

```text
✗ Backend reachable
    endpoint: http://127.0.0.1:1
    hint: start your local model server, or fix `[engine] endpoint` in config
```

```text
summarize: error: the model is not available on the endpoint: ... not found
hint: is the model pulled/loaded on the server? (`ollama run <model>`)
```

Tested against Ollama 0.34.x. The adapter uses `/api/tags` (model list),
`/api/show` (context), `/api/ps` (serving window of the loaded model), and the
OpenAI-compatible `/v1/chat/completions` endpoint; `doctor` reports whichever
of these it can reach.

## Common usage

```sh
summarize article.md
git diff | summarize --profile code
summarize report.pdf --profile academic
summarize notes.txt --format json | jq
summarize notes.txt -o summary.md
summarize incident.txt --context glossary.md   # glossary.md is trusted, incident.txt stays untrusted
summarize notes.txt --dry-run                  # plan only; contacts no backend
summarize --stats article.md                   # timing and token counts on stderr
summarize paper.pdf --verify                   # flag summary numbers/expansions absent from the source
summarize doctor                               # environment diagnostics
```

`summarize -` reads stdin explicitly. With no input argument and a TTY stdin,
`summarize` reports an error instead of waiting forever. Omitting `--profile`
gives the default profile, **`plain`** (see [Profiles](#profiles)).

**One input per invocation.** `summarize a.md b.md` is a usage error; batch
with the shell:

```sh
for f in docs/*.md; do summarize "$f" -o "${f%.md}.summary.md"; done
# or: printf '%s\0' docs/*.md | xargs -0 -n1 summarize
```

## Supported backends

There are no cloud providers. A backend is a local adapter:

| `backend` | Default endpoint | Notes |
| --- | --- | --- |
| `ollama` | `http://localhost:11434` | native model/context discovery |
| `openai-compatible` (alias `openai`) | configured | any local OpenAI Chat Completions server — this covers vLLM, text-generation-webui, and other `/v1` servers |
| `llama.cpp` (alias `llama-cpp`) | `http://localhost:8080` | llama.cpp server |
| `lmstudio` (alias `lm-studio`) | `http://localhost:1234` | LM Studio |

If you never set an endpoint, selecting a backend uses its loopback default.
Capability negotiation (streaming, structured JSON, reasoning control, context
length) is described in [docs/backends.md](docs/backends.md).

## Supported inputs

| Input | Reader | Notes |
| --- | --- | --- |
| `-` or no argument | stdin | bounded; a TTY with no input is an error |
| `.txt`, `.log`, unknown text extensions | text | any file whose content is valid text |
| `.md`, `.markdown`, `.mdown`, `.mkd` | markdown | preserved verbatim; never rendered or fetched |
| `.py`, `.rs`, `.c`, `.go`, `.toml`, … | code | passed through unmodified |
| `.pdf` | PDF (optional extra) | page-aware; scanned PDFs need `--ocr` |

There is **no DOCX/HTML/EPUB reader** — convert those to text first. NUL bytes
are rejected as binary. Details: [docs/readers.md](docs/readers.md).

## Configuration

Configuration is layered (later wins): CLI → `SUMMARIZER_*` environment →
project `./summarizer.toml` (opt-in) → user config → built-in defaults. The
user config lives at `$XDG_CONFIG_HOME/summarizer/config.toml` or
`~/.config/summarizer/config.toml`:

```toml
[engine]
backend = "ollama"
endpoint = "http://localhost:11434"
model = "qwen2.5:3b"
timeout_seconds = 60
```

Or point at a server with a one-off environment variable:

```sh
SUMMARIZER_MODEL=qwen3:8b summarize article.md
SUMMARIZER_BACKEND=llama.cpp SUMMARIZER_ENDPOINT=http://localhost:8080 summarize article.md
```

A project `./summarizer.toml` is read only when you opt in
(`SUMMARIZER_PROJECT_CONFIG=1`), so a cloned repository cannot silently change
your model. Every key, the full precedence rules, the environment-variable
list, and an example file are in
[docs/configuration.md](docs/configuration.md) and
[config.example.toml](config.example.toml).

## Profiles

Built-in profiles: **`plain` (the default)**, `code`, `academic`, and
`meeting`. Add or override one by dropping `NAME.md` into
`~/.config/summarizer/prompts/`. `summarize profiles` lists them with their
source. See [docs/configuration.md](docs/configuration.md#profiles).

## Safety: trusted vs. untrusted input

Document text is untrusted. Each request wraps it in a fresh, random boundary
that is checked against the content first, so boundary-shaped text in a
document cannot close the region early. Map-reduce interim summaries are
treated the same way — as untrusted data, never as instructions.

`--context FILE` is the one input **you** mark as trusted: it is placed in a
separate, labelled section and never mixed with the document. Only pass a file
whose origin you trust, because its content is treated as instructions.

These are structural guarantees, not a claim that prompt injection is solved.
The full threat model is [SECURITY.md](SECURITY.md); the prompt-boundary design
is [docs/design.md](docs/design.md); the local-state and privacy summary is
[docs/security.md](docs/security.md). Note that root `SECURITY.md` also holds
the private vulnerability-reporting policy — that is why there are two
security documents.

## Output and the JSON schema

Default output is Markdown on stdout. `--format json` emits a stable envelope
(`document`, `profile`, `engine`, `strategy`, `chunks`, `summary`,
`chunk_provenance`, `duration_seconds`) with schema and an example in
[docs/readers.md](docs/readers.md#provenance-and-the-json-schema). Provenance
never leaks into plain/Markdown output and never enters trusted prompt
instructions. `--dry-run` reports reader metadata and the prompt identity
without contacting a backend.

## Commands

| Command | Purpose |
| --- | --- |
| `summarize FILE` / `-` | summarize a file or stdin |
| `summarize doctor` | installation, configuration, backend, model, and cache diagnostics |
| `summarize models` | models the configured local backend reports |
| `summarize profiles` | installed profiles and their prompt identity |
| `summarize config show\|path\|validate` | resolved configuration and locations |
| `summarize cache status\|path\|clear` | inspect and clear the optional cache |
| `summarize evaluate` | run the local model/backend evaluation corpus |
| `summarize session start\|end\|status` | named local run sessions |
| `summarize completions bash\|zsh\|fish` | shell completion script |

Options and exit status: [docs/cli.md](docs/cli.md).

## Speed and system load

Latency is dominated by whether the model fits your GPU. Real measurements on
a laptop with a 4 GiB-VRAM GPU and 12 CPU threads:

- `qwen2.5-3b-16k` (1.9 GB, GPU-resident): ~38 tok/s. A 14,000-token paper
  summarized directly in ~21 s; a short note in ~1 s.
- `qwen3:8b` (5.2 GB — too big for 4 GiB VRAM, so Ollama splits it
  ~50/50 CPU/GPU): ~1.2 tok/s, minutes per summary.

For long documents, **prompt processing** (reading the input) is a large part
of the cost; keeping a document in a single direct call is usually faster and
more coherent than map-reduce. If a run is slow, check `ollama ps` for the
CPU/GPU split and the serving context window, and prefer a smaller model that
stays on the GPU. `--stats` reports timing and token estimates on stderr.

## Troubleshooting

- **`connection refused` / `Backend reachable ✗`** — start your model server
  (`ollama serve`, or the llama.cpp/LM Studio server) and confirm the endpoint
  in `summarize doctor`. URL, model, and backend come from config or
  `SUMMARIZER_ENDPOINT` / `SUMMARIZER_MODEL` / `SUMMARIZER_BACKEND`.
- **`the model is not available on the endpoint`** — pull it (`ollama pull
  qwen2.5:3b`) or set `[engine] model` to an installed name. `summarize models`
  lists what the backend reports.
- **Empty summary or "PDF text extraction failed"** — the PDF is probably a
  scan. Install the `ocr` extra plus system `tesseract`/Poppler and pass
  `--ocr`; confirm with `summarize doctor`.
- **`summarize` hangs / times out** — lower `[engine] timeout_seconds` or pass
  `--timeout`; a CPU-bound model can exceed the 60 s default on long inputs.
- **`appears to be binary`** — the file is not text (or a PDF without a
  `.pdf` extension); convert it or rename it to `*.pdf`.
- **No input / TTY** — `summarize` with no argument and a TTY stdin errors
  instead of hanging; pass a file or `-`.

## Documentation

The README is the orientation; the detail lives in [docs/](docs/index.md).

| | |
| --- | --- |
| [Installation](docs/installation.md) | pipx, extras, releases, packaging |
| [Configuration](docs/configuration.md) | precedence, project opt-in, every key, env vars, profiles |
| [CLI reference](docs/cli.md) | commands, discovery, completions, exit status |
| [Backends](docs/backends.md) | adapters and capability negotiation |
| [Performance and local state](docs/performance.md) | progress, cancellation, concurrency, cache, stats |
| [Readers](docs/readers.md) | inputs, PDF/OCR, provenance, JSON schema, limits |
| [Sessions](docs/sessions.md) | named local run records |
| [Design and safety](docs/design.md) | the trust boundary and aggregation |
| [Security and privacy](docs/security.md) | local-only guarantees, local state |
| [Evaluation](docs/evaluation.md) | the local evaluation harness |
| [Summary quality](docs/quality.md) | the first manual claims benchmark, and its caveats |

## Contributing, changelog, license

- Contributing and the repository safety guard: [CONTRIBUTING.md](CONTRIBUTING.md).
  The guard (`scripts/check_repo_safety.py`) is a network-free check that the
  files git would commit contain no credentials, `.env` files, local config,
  absolute home paths, or wildcard binds.
- Version history: [CHANGELOG.md](CHANGELOG.md).
- Design notes and deferred work: [ROADMAP.md](ROADMAP.md).
- License: MIT — see [LICENSE](LICENSE).
