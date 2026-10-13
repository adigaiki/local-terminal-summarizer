# summarizer

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/adigaiki/local-terminal-summarizer/actions/workflows/ci.yml/badge.svg)](https://github.com/adigaiki/local-terminal-summarizer/actions/workflows/ci.yml)

`summarize` is a Linux-and-macOS command-line summarizer that turns a file or
stdin into a concise summary using a **local** LLM server you already run
(Ollama, llama.cpp, LM Studio, or any OpenAI-compatible server). It is a Unix
tool: pipe a document in, get a summary on stdout, with long-document chunking,
a trusted/untrusted input boundary, stable JSON output, and diagnostics.

Nothing leaves your machine by default: every request goes to a model server
over loopback (`http://localhost:11434`). Two opt-ins can produce other
traffic: `--check-update` (one request to a URL you configure in `[update]`),
and a non-loopback `endpoint`, which is refused unless you set
`[engine] allow_non_loopback`. See [docs/security.md](docs/security.md).

- MIT-licensed, Python 3.11+, zero runtime dependencies in the core install
  (PDF and OCR are optional extras). No cloud, no telemetry, no model download,
  no self-update.

## Why not just `ollama run`?

`ollama run` prints whatever the model says, with no structure around it.
`summarize` wraps the same local model with the things a summarizer needs:
documents longer than the context window are chunked and reduced; the document
is wrapped in a fresh random boundary and treated as untrusted data while
`--context` stays trusted; `--format json` gives a stable envelope with chunk
provenance; and `summarize doctor` tells you what is reachable, configured, and
missing. It is one small tool, not a chat client.

## Example

Input (`docs/examples/cache-incident.md`, an original sample), summarized with
the default `plain` profile on `qwen2.5-3b-16k` (Ollama 0.35.0):

```console
$ summarize docs/examples/cache-incident.md
# Cache incident review: Stale Prices During Node Replacement

On 14 March, at 02:40, the night-shift on-call engineer replaced a failing cache node in the `pricing` cluster. The node had been experiencing intermittent timeouts for about twenty minutes, and the replacement was treated as routine. During the swap, the pricing API served roughly four percent of requests from a replica that had not yet caught up. This resulted in some customers seeing prices that were up to an hour old for about three and a half minutes. No orders were placed at the stale prices, as the checkout service re-quotes against the primary node before authorization, and the seven orders that arrived during the window were priced from fresh data.

The on-call engineer noticed a rise in stale-read metrics at 02:52 and rolled the replica back at 03:05. Error rates were normal by 03:20, and the stale-read rate returned to its baseline shortly afterwards.

… (excerpt; the full output also covers impact, cause, and follow-up actions)
```

A different local model reads differently. The `--format json` envelope and the
full schema are in
[docs/readers.md](docs/readers.md#provenance-and-the-json-schema).

## Table of contents

- [Why not just `ollama run`?](#why-not-just-ollama-run)
- [Example](#example)
- [Requirements](#requirements)
- [Install](#install)
- [Quick start (Ollama)](#quick-start-ollama)
- [Common usage](#common-usage)
- [Supported backends](#supported-backends)
- [Supported inputs](#supported-inputs)
- [Configuration](#configuration)
- [Profiles](#profiles)
- [Safety: trusted vs. untrusted input](#safety-trusted-vs-untrusted-input)
- [Verifying a summary](#verifying-a-summary)
- [Output and the JSON schema](#output-and-the-json-schema)
- [Local state](#local-state)
- [Commands](#commands)
- [Evaluation is mechanical](#evaluation-is-mechanical)
- [Speed and system load](#speed-and-system-load)
- [Troubleshooting](#troubleshooting)
- [When not to use this](#when-not-to-use-this)
- [How this differs from other tools](#how-this-differs-from-other-tools)
- [Documentation](#documentation)
- [Contributing, changelog, license](#contributing-changelog-license)

## Requirements

- **Python 3.11 or newer.**
- **A local model server** with at least one model pulled. The default backend
  is [Ollama](https://ollama.com/); see [Quick start](#quick-start-ollama).
- **A model that fits your machine.** A 3B–8B instruct model is plenty. A Q4
  model needs roughly its download size of RAM; if it fits in GPU memory it is
  fast, and if it spills to CPU it can drop to ~1 token/second. `ollama ps`
  shows the CPU/GPU split and the serving window.
- **Platforms:** tested in CI on **Linux** and **macOS** (both run the full
  suite); WSL is expected to work. The code uses only POSIX APIs (`flock`,
  signals). Native Windows is unsupported.

## Install

Not on PyPI yet (planned distribution name: `local-terminal-summarizer`).
Install from a checkout with
[pipx](https://pipx.pypa.io/) (isolated, puts `summarize` on your `PATH`):

```sh
git clone https://github.com/adigaiki/local-terminal-summarizer.git
cd local-terminal-summarizer
pipx install .            # or: uv tool install .
```

From a virtualenv instead: `python3 -m venv .venv &&
.venv/bin/python -m pip install -e '.[test]'`. Optional extras (the base
install stays dependency-free): `pipx install '.[pdf]'` for PDF text and
`pipx install '.[ocr]'` for OCR (which also needs system `tesseract` and
Poppler). Uninstall with `pipx uninstall local-terminal-summarizer`.

Releases, packaging, and TestPyPI-first publishing are in
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
are installed. A healthy run looks like this (with the custom 16k variant from
the note below configured):

```text
✓ Backend reachable — http://localhost:11434
✓ Model available — configured model 'qwen2.5-3b-16k' is installed
✓ Context window known — 16384 tokens (server)
! OCR — optional, not installed

Everything looks good.
```

The full output, and what a failure looks like, are in
[docs/troubleshooting.md](docs/troubleshooting.md#what-doctor-prints).

**The window `doctor` reports is state-dependent.** With the model *unloaded*,
Ollama answers with the model's **trained maximum** (`/api/show`); once it is
loaded it serves a smaller window, visible as the `CONTEXT` column of
`ollama ps`. A plain `ollama pull qwen2.5:3b` reports a trained maximum of
32768 while a 4 GiB GPU serves 4096. A normal run warms the model with one
tiny request and re-reads the *served* window before planning, so it does not
send an over-large prompt; `doctor` never warms anything and shows a `!`
"Serving context window" line when it only has the trained maximum.

To give a model a larger served window, create a derived model (verified):

```sh
cat > Modelfile <<'EOF'
FROM qwen2.5:3b
PARAMETER num_ctx 16384
EOF
ollama create qwen2.5-3b-16k -f Modelfile
```

Tested against Ollama 0.35.0. The adapter uses `/api/tags` (model list),
`/api/show` (context), `/api/ps` (serving window of the loaded model), and the
OpenAI-compatible `/v1/chat/completions` endpoint; `doctor` reports whichever
of these it can reach.

## Common usage

```sh
summarize article.md
git diff | summarize --profile code
summarize report.pdf --profile academic
summarize scan.pdf --ocr                      # OCR a scanned PDF (optional extra)
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

Selecting a backend without setting an endpoint uses its loopback default.
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

Or override for one run: `SUMMARIZER_MODEL=qwen3:8b summarize article.md`.
A project `./summarizer.toml` is read only when you opt in
(`SUMMARIZER_PROJECT_CONFIG=1`). Every key, the precedence rules, the
environment variables, and a full example are in
[docs/configuration.md](docs/configuration.md) and
[config.example.toml](config.example.toml).

## Profiles

Built-in profiles: **`plain` (the default)**, `code`, `academic`, and
`meeting`. Add or override one by dropping `NAME.md` into
`~/.config/summarizer/prompts/`. `summarize profiles` lists them. See
[docs/configuration.md](docs/configuration.md#profiles).

## Safety: trusted vs. untrusted input

Document text is untrusted: each request wraps it in a fresh random boundary
checked against the content first, and map-reduce interim summaries get the
same treatment. `--context FILE` is the one input **you** mark as trusted — it
is kept in a separate labelled section, never mixed with the document.

These are structural guarantees, not a solved problem. See
[SECURITY.md](SECURITY.md) (threat model and private vulnerability reporting —
hence two security docs), [docs/design.md](docs/design.md), and
[docs/security.md](docs/security.md).

## Verifying a summary

`--verify` reports numbers and `ACRONYM (expansion)` phrases in the summary
that do not appear in the source: grep, not a judge model, and advisory (exit
status unchanged). It does not catch a real number on the wrong label or
non-numeric fabrication. Details: [docs/verify.md](docs/verify.md).

## Output and the JSON schema

Default output is Markdown on stdout. `--format json` emits a stable envelope
(`document`, `profile`, `engine`, `strategy`, `chunks`, `summary`,
`chunk_provenance`, `duration_seconds`; `--verify` adds `verification`, and a
detected truncation adds `truncated`). Schema and example:
[docs/readers.md](docs/readers.md#provenance-and-the-json-schema). Provenance
never leaks into plain/Markdown output and never enters trusted prompt
instructions.

## Local state

`summarize` writes nothing by default. Two optional, plain-file stores:
the **cache** (`summarize cache status|path|clear`; off by default; derived
summaries only, keyed by content hash + model/profile, under
`~/.cache/summarizer`, never document text or paths) and **sessions**
(`summarize session start|end|status`; pointers only, recorded only when
explicitly selected). See [docs/performance.md](docs/performance.md) and
[docs/sessions.md](docs/sessions.md).

## Commands

| Command | Purpose |
| --- | --- |
| `summarize FILE` / `-` | summarize a file or stdin |
| `summarize doctor` | installation, configuration, backend, model, and cache diagnostics |
| `summarize models` | models the configured local backend reports |
| `summarize profiles` | installed profiles and their prompt identity |
| `summarize config show\|path\|validate` | resolved configuration and locations |
| `summarize cache status\|path\|clear` | inspect and clear the optional cache (off by default) |
| `summarize evaluate` | run the local model/backend evaluation corpus |
| `summarize session start\|end\|status` | group runs under a named local record |
| `summarize completions bash\|zsh\|fish` | shell completion script |

Every option (including `--verify`, `--context`, `--dry-run`, `--stats`,
`--check-update`, `-o`) and the exit status are in [docs/cli.md](docs/cli.md).

## Evaluation is mechanical

`summarize evaluate` applies **mechanical** checks (valid format/JSON, sentinel
retention, marker absence, strategy, bounds) — not objective quality scores,
and no judge model. Whether summaries are actually good is addressed, in part
and with caveats, in [docs/quality.md](docs/quality.md); the harness is
[docs/evaluation.md](docs/evaluation.md).

## Speed and system load

Latency is dominated by whether the model fits your GPU. Measured on a laptop
with a 4 GiB-VRAM GPU: `qwen2.5-3b-16k` (GPU-resident) summarized a
14,000-token paper directly in ~21 s; `qwen3:8b`, which does not fit and spills
to CPU, runs ~1.2 tok/s. Keeping a document in one direct call is usually
faster and more coherent than map-reduce. The CLI is GPU-agnostic; the numbers
and the full picture are in [docs/performance.md](docs/performance.md).

## Troubleshooting

- **`connection refused` / `Backend reachable ✗`** — start your model server
  (`ollama serve`, or the llama.cpp/LM Studio server) and confirm the endpoint
  in `summarize doctor`. URL, model, and backend come from config or
  `SUMMARIZER_ENDPOINT` / `SUMMARIZER_MODEL` / `SUMMARIZER_BACKEND`.
- **`the model is not available on the endpoint`** — pull it (`ollama pull
  qwen2.5:3b`) or set `[engine] model` to an installed name. `summarize models`
  lists what the backend reports.
- **`the backend processed ... prompt tokens ... truncated`** (or
  `"truncated": true`) — the prompt was likely truncated; see
  [docs/troubleshooting.md](docs/troubleshooting.md) for the fix and the
  streaming caveat.

Everything else — PDFs and OCR, timeouts, context overflow, binary files, the
no-input error — is in [docs/troubleshooting.md](docs/troubleshooting.md).

## When not to use this

`summarize` produces a probabilistic summary from a local model. It is the
wrong tool when being wrong is expensive:

- **Where a wrong number or claim matters** (legal, medical, financial,
  safety-critical). `--verify` is a grep, not fact-checking, and a clean report
  is not proof; treat `--format json` as untrusted at any automation boundary.
- **Wide tables on a small model.** Table structuring fixes column
  *assignment*, not judgment; a 3B model can still garble an 8-column table.
- **Documents needing reasoning across the whole text.** Map-reduce summarizes
  chunks independently, so a term defined in one part and used far away can be
  lost; prefer a model whose context holds the document in one call
  (`--dry-run` shows the plan, `--strict` refuses to chunk).
- **Content you would not send to a local model.** The tool does not redact;
  the model sees the document.

The honest state of summary quality is in [docs/quality.md](docs/quality.md).

## How this differs from other tools

Not better — different. `summarize` is a summarization pipeline with an
explicit trust boundary, strict context budgeting, and validated output;
general-purpose tools may fit better if you need chat, plugins, or cloud
models. Claims below are from each project's own README.

| Tool | What it is | How `summarize` differs |
| --- | --- | --- |
| `ollama run` | One prompt against a local model | No chunking, budgeting, trust boundary, or output validation |
| [`llm`](https://github.com/simonw/llm) | CLI/Python for many models, remote and local (Ollama via a plugin), with SQLite logging, embeddings, schemas and tools | General-purpose; long-document summarization is on you |
| [`fabric`](https://github.com/danielmiessler/fabric) | A library of reusable prompts ("Patterns", including `summarize`) over many providers, with a REST API | Prompt-library-first; `summarize` is pipeline-first and local-only |

## Documentation

The README is the orientation; the detail lives in [docs/](docs/index.md):
[Installation](docs/installation.md) · [Configuration](docs/configuration.md) ·
[CLI](docs/cli.md) · [Backends](docs/backends.md) ·
[Performance and local state](docs/performance.md) · [Readers](docs/readers.md) ·
[Claim verification](docs/verify.md) · [Troubleshooting](docs/troubleshooting.md) ·
[Sessions](docs/sessions.md) · [Design and safety](docs/design.md) ·
[Security and privacy](docs/security.md) · [Evaluation](docs/evaluation.md) ·
[Summary quality](docs/quality.md).

## Contributing, changelog, license

- Contributing and the repository safety guard: [CONTRIBUTING.md](CONTRIBUTING.md).
- Version history: [CHANGELOG.md](CHANGELOG.md).
- Design notes and deferred work: [ROADMAP.md](ROADMAP.md).
- License: MIT — see [LICENSE](LICENSE).
