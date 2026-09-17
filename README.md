# summarizer

`summarize` is a Linux-first terminal program for summarizing files and
standard input through a local LLM server. Its normal summarization path makes
no internet requests and has no runtime dependencies beyond Python 3.11+.

The default backend is [Ollama](https://ollama.com/) at
`http://localhost:11434`, using `llama3.1:8b`. Any local server implementing
the OpenAI Chat Completions API can be selected with `backend = "openai"`.

## Install and run

From a checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
ollama pull llama3.1:8b
printf 'Mercury is the closest planet to the Sun.' | .venv/bin/summarize
```

Once installed, typical use is:

```sh
summarize article.md
git diff | summarize --profile code
summarize report.pdf --profile academic
summarize notes.txt --format json | jq
summarize notes.txt -o summary.md
summarize notes.txt --dry-run
summarize doctor
```

`summarize -` explicitly reads stdin. When stdin is a TTY, running
`summarize` with no input reports an error instead of waiting forever.

## Configuration

Configuration is layered, with later sources taking precedence:

1. command-line options
2. `SUMMARIZER_*` environment variables
3. `./summarizer.toml`
4. `$XDG_CONFIG_HOME/summarizer/config.toml` (or `~/.config/summarizer/config.toml`)
5. built-in defaults

Example `summarizer.toml`:

```toml
[engine]
backend = "ollama"              # or "openai" / "openai-compatible"
endpoint = "http://localhost:11434"
model = "llama3.1:8b"
timeout_seconds = 60
retries = 1
# Set this when the backend cannot report its context length.
context_length = 0
max_tokens = 1024              # hard cap per map/reduce generation
reasoning_effort = "none"      # none, low, medium, or high when supported

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
`SUMMARIZER_MAX_TOKENS`, `SUMMARIZER_REASONING_EFFORT`,
`SUMMARIZER_PROFILE`, and `SUMMARIZER_FORMAT`.

`reasoning_effort` defaults to `none`, appropriate for ordinary summaries.
It is sent only to backends that advertise support for compatible reasoning
control; the generic OpenAI-compatible backend omits the optional field.
Every request sends `max_tokens`, so generation is bounded even when a model
has a large context window.

Built-in profiles are `plain`, `code`, `academic`, and `meeting`. Put a
`NAME.md` profile in `~/.config/summarizer/prompts/` to override or add one.
Profiles must contain exactly one of `{{input}}` or `{{summaries}}`; optional
placeholders are `{{context}}`, `{{lang}}`, and `{{output_format}}`.

## Design and safety

Every reader produces the same `Document` object, including content,
provenance, MIME type, encoding, size, and reader metadata. Text, Markdown,
code, PDF, and stdin therefore share one pipeline.

Document text is untrusted data. For every model request it is put in a fresh,
cryptographically random XML-like boundary, separate from trusted security
instructions and the selected profile. Map-reduce intermediate summaries are
also boundary-scoped. `--context FILE` is explicitly user-supplied trusted
supplemental context and receives its own labelled section; it is never mixed
with the document.

Oversized input is budgeted against the model's *discovered* context window
(via the local server's metadata when it can be read, otherwise your configured
`context_length`, otherwise a conservative fallback), then chunked at
paragraph/sentence/word boundaries with overlap and summarized via map-reduce.
Use `--chunk-strategy tokens|chars` to pick the sizing unit, and `--strict` to
refuse chunking entirely instead of degrading to map-reduce. Input bytes,
lines, and chunk count are hard-limited, and chunk provenance (source, index,
offsets, boundary) is preserved and included in `--format json`. `--dry-run`
reads and plans the request without probing or contacting an engine.

## Configuration files and secrets

Keep your real configuration outside Git: `~/.config/summarizer/config.toml`
or `./summarizer.toml` are both gitignored, and `config.example.toml` is the
only committed example and contains placeholders only. Point `endpoint` at
your own local engine; the project ships no credentials, cloud endpoints, or
API keys. See `SECURITY.md` for the trust model and `ROADMAP.md` for the
long-document design notes.

All diagnostics and progress are written to stderr; stdout contains only the
summary or requested JSON. `-o FILE` writes through a same-directory temporary
file and atomically replaces the destination only after a successful result.

PDF extraction needs `pip install 'summarizer[pdf]'`. OCR is opt-in (`--ocr`)
and additionally needs `pip install 'summarizer[ocr]'` plus Poppler's
`pdftoppm` command.

## Exit status

| Code | Meaning |
| ---: | --- |
| 0 | success |
| 1 | input or usage error |
| 2 | local engine error |
| 3 | configuration error |
| 4 | explicit update-check error |
| 130 | interrupted with Ctrl-C |

`--check-update` is the only update-related operation. It is explicit,
check-only, and requires a configured update URL; normal summarization never
checks for updates or contacts the network.
