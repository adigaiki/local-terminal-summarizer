# Evaluation

A small deterministic harness runs the same corpus against different local
models and backends.

```sh
summarize evaluate --dry-run                 # list cases, contact nothing
summarize evaluate --model qwen3:8b          # run everything
summarize evaluate --model qwen3:8b --tags core,code,security
summarize evaluate --model qwen3:8b --format json
```

## The checks are mechanical, not quality scores

> **They are not objective quality scores, and no external judge model is
> used.** A failing check is a prompt to look at the model, not a verdict.

The checks are structural and observable: valid output format, valid JSON,
retention of a sentinel, absence of an echoed injection marker, expected
aggregation strategy, and bounded output size — plus latency and estimated
output tokens as observations.

`--dry-run` is what CI uses, so the framework needs no model at all.
Evaluation is separate from normal summarization.

## What this does not measure

**Summary quality is not measured.** The checks confirm the output is
well-formed and structurally safe; they say nothing about factual coverage,
omission, or usefulness. A summary can pass every check and still be bad.

There is no reference-based quality benchmark yet. That gap is the most
important one in the project, and [quality.md](quality.md) describes the
planned benchmark and the honest current state.

## Layout

```text
evaluation/
  fixtures/     documents used by cases
  cases/        case definitions (*.toml)
  runner.py     re-export of summarizer.evaluation.runner
  report.py     re-export of summarizer.evaluation.report
```

## Notes

- The first case may cold-load the model; raise `--timeout` if it times out
  (for example `--timeout 240`).
- Cases run with `stream = false` so latency and output size are measured
  rather than printed.
- The corpus lives in the repository's `evaluation/` directory. When the
  package is installed on its own, point the harness at a checkout with
  `--eval-dir PATH` or `$SUMMARIZER_EVAL_DIR`.

## Manual check: silent prompt truncation (Ollama)

Reproduce the failure the truncation guard exists to catch. With a small model
whose served window is far below its trained maximum:

```sh
ollama pull qwen2.5:3b            # trained maximum 32768
ollama stop qwen2.5:3b            # ensure it is unloaded
ollama ps                         # empty

# A document larger than the served window (4096 on a 4 GiB GPU), with a
# unique fact at the very START and a question never asked:
python3 - <<'PY'
text = "Project codename ARGONAUT-9 uses a chromium catalyst.\n\n" \
     + "The quick brown fox jumps over the lazy dog. " * 400 \
     + "\n\nThe report concludes the final yield was 97 percent.\n"
open("/tmp/argonaut.txt", "w").write(text)
PY

summarize --model qwen2.5:3b --no-progress --max-tokens 200 /tmp/argonaut.txt
ollama ps                         # CONTEXT column: the real served window
```

Without the guard this planned against the 32768 trained maximum, sent one
oversized prompt, and Ollama silently dropped the *beginning* — the tail-only
summary (`"the report ... concludes ... 97 percent"`, no `ARGONAUT-9`) came
back with exit 0. With the guard the run warms the model, reads the served
window from `/api/ps`, chunks to fit, and does not truncate. Detected
truncation that survives the retries sets `truncated: true` in `--format json`
and a warning on stderr.
