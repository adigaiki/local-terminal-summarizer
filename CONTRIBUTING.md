# Contributing

Thanks for helping. This is a local-first, Unix-native tool: keep changes
small, explicit, and testable without a model server.

## Development setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

The base install has zero runtime dependencies; `[test]` adds pytest and
pypdf so the PDF reader tests can run. Nothing downloads a model.

## Before you push

```sh
.venv/bin/python -m pytest            # full suite, no network, no Ollama
.venv/bin/python -m compileall -q src tests evaluation scripts
.venv/bin/python scripts/check_repo_safety.py
.venv/bin/python -m build --no-isolation   # optional: sdist + wheel
git diff --check
```

CI runs pytest and `compileall` on Python 3.11 and 3.14, the repository
safety guard, local CLI smoke tests, and a packaging job that builds the
distributions. It never needs Ollama, a GPU, or model access. Tests must bind
and connect only to loopback (`tests/conftest.py` enforces this).

## Real-model validation (manual, optional)

When a local Ollama is available, sanity-check against a real model:

```sh
.venv/bin/summarize doctor
.venv/bin/summarize models
.venv/bin/summarize evaluate --model qwen3:8b --tags core
```

`evaluate --dry-run` is the CI-safe path; the rest is manual integration, not
required for a contribution to be accepted.

## Ground rules

- **Backend names stay out of the pipeline.** If behaviour differs between
  runtimes, put it in an adapter or express it as a capability in
  `engine/capabilities.py`; never add `if backend == ...` to the pipeline.
- **Adding a local backend** is a `BackendSpec` entry in
  `engine/backends.py` plus, only when needed, one small adapter class. No
  cloud providers.
- **Untrusted data stays untrusted.** Document text, reader metadata, and
  map-stage interim summaries are always wrapped in a boundary that is
  checked against the content. The reduce instructions and interim summaries
  must remain structurally separate. `--context` is the only user-trusted
  input and is still boundary-checked.
- **Bounded output.** Every generation request carries `max_tokens`; never
  ship a path that can generate unbounded output.
- **Local only.** No telemetry, analytics, cloud endpoints, remote URLs in
  normal runtime behaviour, remote log upload, or model downloads. Test
  servers bind `127.0.0.1`.
- **No secrets in the repo.** Keep real config in
  `~/.config/summarizer/config.toml`; `config.example.toml` is placeholders
  only. The safety guard allows exceptions only with a written reason.

## Scope

Out of scope unless a concrete need appears: GUI, URL fetching, cloud models,
agents, tool execution, embeddings/vector databases, self-update, background
services, automatic model installation, multi-model ensemble/routing, and
refine-based aggregation. See `ROADMAP.md`.

## Tests

Prefer in-process test engines (see `tests/test_pipeline_and_cli.py`) over a
live server. Structural security guarantees belong in
`tests/test_adversarial_corpus.py`; evaluation behaviour in
`tests/test_evaluation.py`; configuration precedence in
`tests/test_config_layering.py`; cancellation in `tests/test_cancellation.py`;
concurrency in `tests/test_concurrency.py`; caching/checkpoints and prompt
identity in `tests/test_cache_checkpoints.py` and
`tests/test_prompt_identity.py`; progress in `tests/test_progress.py`; output
and stats in `tests/test_output_behavior.py`; packaging/version in
`tests/test_packaging.py`.
