# summarizer documentation

The [README](../README.md) is the short orientation. Everything else lives
here.

| Document | Contents |
| --- | --- |
| [Installation](installation.md) | installing from a checkout, optional extras, releases, packaging |
| [Configuration](configuration.md) | layering and precedence, project opt-in, every config key, profiles, environment variables |
| [CLI reference](cli.md) | commands, model/config/cache discovery, shell completions, exit status |
| [Backends](backends.md) | the adapter interface, capability negotiation, supported local runtimes |
| [Performance and local state](performance.md) | progress, cancellation, bounded concurrency, cache/checkpoints, `--stats` |
| [Readers](readers.md) | supported inputs, optional PDF/OCR, provenance, the JSON schema, resource limits |
| [Claim verification](verify.md) | `--verify`: what it checks, and what it misses |
| [Sessions](sessions.md) | named local run records, selection rules, what is stored |
| [Design and safety](design.md) | the trust boundary, boundary-collision handling, map-reduce intermediates, chunking |
| [Security and privacy](security.md) | local-only guarantees, local state, the repository safety guard |
| [Evaluation](evaluation.md) | the local model/backend evaluation harness |
| [Summary quality](quality.md) | the first manual claims benchmark, and its caveats |

Project policies: [SECURITY.md](../SECURITY.md) (threat model) and
[ROADMAP.md](../ROADMAP.md) (design notes and deferred work).
