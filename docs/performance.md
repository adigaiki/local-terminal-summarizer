# Performance and local state

## Progress

Progress is written to **stderr** only, so stdout stays composable (a pipe to
`jq` or `head` is unaffected). On an interactive terminal it draws a single
updating bar (one line per chunk, never one per token); on a non-TTY it prints
a start line and a completion line. `--quiet` disables it, as does
`[defaults] progress = false`; `--no-progress` disables it for one run. JSON on
stdout stays valid regardless.

```text
Summarizing paper.pdf
[██████────] 6/10 chunks · qwen3:8b · 31s
```

## Cancellation

Ctrl-C is a first-class cancellation. The signal cancels the current run, the
engine observes it while reading or streaming, remaining map chunks are
stopped, and the program exits with status 130 without a traceback. Output
files are written through an atomic temporary file, so an interruption cannot
corrupt an existing file, and an interrupted run is not recorded into a
session. Streaming requests are interrupted promptly; a non-streaming request
that has already been sent is bounded by `--timeout`.

## Bounded concurrency

Independent map chunks can run with bounded concurrency (default `1`, which is
exactly the sequential behavior):

```toml
[chunking]
concurrency = 1   # 1..8; higher is not automatically faster
```

Results are always assembled in chunk order, so provenance and the reduce stage
are deterministic. The reduce stage waits until all required map results are
available. Whether concurrency helps depends on the backend, and it is easy to
assume a speedup that is not there. `--stats` is the way to check.

### Measured

One machine, `qwen3:8b` via Ollama, a 9-chunk synthetic report, `--stats`:

| `[chunking] concurrency` | map | reduce | total |
| ---: | ---: | ---: | ---: |
| 1 | 126.7s | 70.0s | 196.7s |
| 2 | 126.6s | 75.6s | 202.2s |

Concurrency made no measurable difference: Ollama serves one request at a time
for a single model, so the work is serialized regardless. This is one model,
one machine, and one document — a cautionary example, not a benchmark. If your
backend can genuinely serve requests in parallel, `--stats` will show it;
otherwise leave `concurrency = 1`.

## Caching and resumable runs

A long map-reduce run can be checkpointed so a failure does not discard the
completed map work. Caching is **off by default** and stores only derived
summaries and hashes — never document contents or source paths:

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
output format, or the language — and the old entry is not reused. A partial
entry is a checkpoint: re-running the same command resumes the completed chunks
and fills in the rest. A resumed run refuses to reuse an incompatible entry.

```sh
summarize cache status      # mode, path, entry count, size
summarize cache path
summarize cache clear
```

Directories are `0700` and files `0600`. `summarize cache clear` removes
everything. See [security.md](security.md) for the privacy model.

### Measured

Same document, 14 chunks, `qwen3:8b`, `[cache] mode = "readwrite"`:

| run | map | reduce | total |
| --- | ---: | ---: | ---: |
| cold | 167.9s | 91.5s | 259.4s |
| warm | 0.0s | 89.2s | 89.6s |

The warm run reused all 14 map results (`reused: 14 cached chunk(s)`). The
reduce stage is not cached and still runs. One machine, one model, one
document; the point is the shape of the saving, not the exact seconds.

## Prompt identity

Checkpoints and cache entries must be able to tell whether a previous result
used the same prompt. Hashing the profile *name* is not enough — the user's
profile file may have changed. The identity hashes the effective template
text, its source kind, the prompt schema version, and the run parameters that
change the rendered prompt (language, output format, presence of trusted
context). It is a short hex digest that never contains prompt text, so it is
safe to print and store.

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
are estimates, and backend-reported numbers are not comparable across
backends.
