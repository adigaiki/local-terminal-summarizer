# summarizer

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/adigaiki/local-terminal-summarizer/actions/workflows/ci.yml/badge.svg)](https://github.com/adigaiki/local-terminal-summarizer/actions/workflows/ci.yml)

`summarize` is a Linux-first command-line summarizer for a local LLM server.
The package is `summarizer`, the command is `summarize`, and the extras are
`summarizer[pdf]` / `summarizer[ocr]`. MIT-licensed, no runtime dependencies
beyond Python 3.11+, and normal summarization makes no network requests.

## Example

Input (`incident.txt`):

```text
At 02:40 the night-shift team replaced a failing cache node. During the swap
the API served about 4 percent of requests from a stale replica, so some users
saw prices up to an hour old. The team rolled back the replica at 03:05 and
error rates were normal by 03:20. Two follow-up actions were filed: add a
staleness check before serving from a replica, and shorten the cache TTL during
node replacements.
```

```console
$ summarize incident.txt
At 02:40, the night-shift team replaced a failing cache node. During the swap,
the API served approximately 4 percent of requests from a stale replica,
resulting in some users seeing prices up to an hour old. The team rolled back
the replica at 03:05, and error rates returned to normal by 03:20. As follow-up
actions, the team filed two items: implementing a staleness check before serving
from a replica, and reducing the cache TTL during node replacements.
```

That is a real run of the default `plain` profile on `qwen3:8b`; another local
model will read differently.

## Install

Not on PyPI yet; install from a checkout:

```sh
git clone https://github.com/adigaiki/local-terminal-summarizer.git
cd local-terminal-summarizer
pipx install .
```

The default backend is [Ollama](https://ollama.com/) at
`http://localhost:11434`; point `endpoint` at your own local server. Extras and
releases: [docs/installation.md](docs/installation.md).

## Common usage

```sh
summarize article.md
git diff | summarize --profile code
summarize report.pdf --profile academic
summarize notes.txt --format json | jq
summarize notes.txt -o summary.md
summarize notes.txt --dry-run          # plan only; contacts no engine
summarize --stats article.md           # timing and token counts on stderr
summarize doctor                       # environment diagnostics
```

`summarize -` reads stdin explicitly. With no input argument and a TTY stdin,
`summarize` reports an error instead of waiting forever.

## Commands

| Command | Purpose |
| --- | --- |
| `summarize FILE` / `-` | summarize a file or stdin |
| `summarize doctor` | installation, configuration, engine, model, and cache diagnostics |
| `summarize models` | models the configured local backend reports |
| `summarize profiles` | installed profiles and their prompt identity |
| `summarize config show\|path\|validate` | resolved configuration and locations |
| `summarize cache status\|path\|clear` | inspect and clear the optional cache |
| `summarize evaluate` | run the local model/backend evaluation corpus |
| `summarize session start\|end\|status` | named local run sessions |
| `summarize completions bash\|zsh\|fish` | shell completion script |

## Safety

Document text is untrusted. Each request wraps it in a fresh, random boundary
that is checked against the content first, so boundary-shaped text in a
document cannot close the region early. Map-reduce interim summaries are
treated the same way — as untrusted data, never as instructions. `--context
FILE` is the one input you mark as trusted, and it stays structurally separate
from the document. These are structural guarantees, not a claim that prompt
injection is solved. See [docs/design.md](docs/design.md) and
[SECURITY.md](SECURITY.md).

## Evaluation

The evaluation harness compares local models on the same corpus. Its checks are
mechanical — valid format, valid JSON, a retained sentinel, no echoed injection
marker, expected strategy, bounded output — and **they are not objective
quality scores**; no external judge model is involved. Summary quality itself
is not measured yet; see [docs/quality.md](docs/quality.md) for the honest
state and the planned benchmark.

## Documentation

The README is the orientation; the detail lives in [docs/](docs/index.md).

| | |
| --- | --- |
| [Installation](docs/installation.md) | pipx, extras, releases |
| [Configuration](docs/configuration.md) | precedence, project opt-in, every key, profiles |
| [CLI reference](docs/cli.md) | commands, discovery, completions, exit status |
| [Backends](docs/backends.md) | adapters and capability negotiation |
| [Performance and local state](docs/performance.md) | progress, cancellation, concurrency, cache, stats |
| [Readers](docs/readers.md) | inputs, PDF/OCR, provenance, JSON schema, limits |
| [Sessions](docs/sessions.md) | named local run records |
| [Design and safety](docs/design.md) | the trust boundary and aggregation |
| [Security and privacy](docs/security.md) | local-only guarantees, local state, repo guard |
| [Evaluation](docs/evaluation.md) | the local evaluation harness |

Contributing: [CONTRIBUTING.md](CONTRIBUTING.md). Design notes and deferred
work: [ROADMAP.md](ROADMAP.md).

## License

MIT — see [LICENSE](LICENSE).
