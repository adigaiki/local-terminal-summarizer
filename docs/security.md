# Security and privacy

The full threat model is in [SECURITY.md](../SECURITY.md). This page is the
practical summary.

## Local-only core

Normal summarization sends nothing off your machine: it talks only to the
local backend over loopback. There is no telemetry, analytics, cloud API,
remote logging, or model download. The backend endpoint defaults to
`http://localhost:11434`; point it at whatever local server you run.

`--check-update` is the only network operation, and only when you configure a
URL yourself. `summarize models`, `doctor`, and `evaluate` contact only the
configured local endpoint; `evaluate --dry-run` contacts nothing.

## Configuration and secrets

Keep your real configuration outside Git: `~/.config/summarizer/config.toml`
and `./summarizer.toml` are gitignored, and `config.example.toml` is the only
committed example, containing placeholders only. `summarize config show`
attributes values to their source and redacts endpoint credentials.

## Local state

Two optional pieces of local state exist, both plain files, both `0700`/`0600`:

- **Sessions** ([docs/sessions.md](sessions.md)): pointers only — no document
  or model text — but they do record *which files you summarized and when*.
- **Cache/checkpoints** ([docs/performance.md](performance.md#caching-and-resumable-runs)):
  derived summaries and hashes only — no document contents, no source paths —
  bounded, and cleared with `summarize cache clear`.

Minimization applies to *contents*, not to metadata: the session root reveals
your file activity even though it stores none of the text. Treat both locations
like dotfiles and keep them outside synced or shared locations if that matters.

## Repository safety guard

`scripts/check_repo_safety.py` is a small, standard-library-only check that
makes it hard to commit API keys, tokens, passwords, cookies, private keys,
`.env` files, local `summarizer.toml`, absolute home paths, temporary
artifacts, or a wildcard bind address. It scans the files git would include,
uses no network, and prints an explainable `path:line: rule` for each finding.
False positives are allowlisted with a reason in `scripts/leak_allowlist.txt`;
malformed allowlist entries are reported rather than silently ignored.

```sh
python scripts/check_repo_safety.py
```
