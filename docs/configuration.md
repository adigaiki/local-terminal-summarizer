# Configuration

Configuration is layered, with later sources taking precedence:

1. command-line options
2. `SUMMARIZER_*` environment variables
3. `./summarizer.toml` (project config — **opt-in**)
4. `$XDG_CONFIG_HOME/summarizer/config.toml` (or `~/.config/summarizer/config.toml`)
5. built-in defaults

## Project config is opt-in

Project config is opt-in and predictable, so a cloned repository cannot
silently force a surprising model, endpoint, or profile on you. `./summarizer.toml`
is read only when you ask for it:

```sh
SUMMARIZER_PROJECT_CONFIG=1 summarize article.md        # read ./summarizer.toml
SUMMARIZER_PROJECT_CONFIG=./team.toml summarize a.md    # read an explicit file
```

Unset, no project file is read. When it is read, it keeps its place in the
precedence chain above. `--dry-run` and `summarize doctor` show which layer
supplied each engine setting.

## Example `summarizer.toml`

See [config.example.toml](../config.example.toml) for the commented version.

```toml
[engine]
backend = "ollama"              # ollama, openai-compatible, llama.cpp, lmstudio
endpoint = "http://localhost:11434"
model = "llama3.1:8b"
timeout_seconds = 60
retries = 1
context_length = 0             # 0: discover from the local server when possible
max_tokens = 1024              # hard cap per map/reduce generation
reasoning_effort = "none"      # none, low, medium, or high when supported
# `reasoning` is accepted as a generic alias for `reasoning_effort`.

[defaults]
profile = "plain"
output_format = "markdown"
stream = true
progress = true
chunk_strategy = "auto"         # auto, tokens, or chars

[chunking]
max_tokens_per_chunk = 3000
overlap_tokens = 200
reserve_output_tokens = 1000
max_chunks = 256
concurrency = 1                 # 1..8; see docs/performance.md

[input]
max_bytes = 52428800
max_lines = 1000000
encoding = "utf-8"

[cache]
mode = "off"                    # off | read | write | readwrite
dir = "~/.cache/summarizer"
```

## Environment variables

`SUMMARIZER_BACKEND`, `SUMMARIZER_ENDPOINT`, `SUMMARIZER_MODEL`,
`SUMMARIZER_TIMEOUT`, `SUMMARIZER_RETRIES`, `SUMMARIZER_MAX_TOKENS`,
`SUMMARIZER_MAX_RESPONSE_BYTES`, `SUMMARIZER_REASONING_EFFORT` (alias
`SUMMARIZER_REASONING`), `SUMMARIZER_PROFILE`, `SUMMARIZER_FORMAT`,
`SUMMARIZER_CONCURRENCY`, `SUMMARIZER_PROGRESS`, `SUMMARIZER_CACHE`,
`SUMMARIZER_CACHE_DIR`.

Two more control discovery rather than a value: `SUMMARIZER_PROJECT_CONFIG`
opts into project config, and `SUMMARIZER_CONFIG` overrides the user config
path.

## Reasoning

`reasoning_effort` defaults to `none`, appropriate for ordinary summaries. It
is sent only to backends that advertise compatible reasoning control; a
requested value is omitted (with a verbose warning) when the backend does not
advertise it, rather than risking an incompatible request.

Every request sends `max_tokens`, so generation is bounded even when a model
has a large context window. Response accumulation is capped at
`max_response_bytes` (default 32 MiB; `0` disables), so a misbehaving local
server cannot grow memory without bound.

## Profiles

Built-in profiles are `plain`, `code`, `academic`, and `meeting`. Put a
`NAME.md` profile in `~/.config/summarizer/prompts/` to override or add one.
Profiles must contain exactly one of `{{input}}` or `{{summaries}}`; optional
placeholders are `{{context}}`, `{{lang}}`, and `{{output_format}}`.

Each profile has a content-addressed *prompt identity* (see
[performance.md](performance.md#prompt-identity)) used by the cache so a
changed profile invalidates stale results. `summarize profiles` lists them.

## Secrets

Keep your real configuration outside Git: `~/.config/summarizer/config.toml`
and `./summarizer.toml` are both gitignored, and `config.example.toml` is the
only committed example, containing placeholders only. See
[security.md](security.md).
