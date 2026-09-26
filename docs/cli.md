# CLI reference

## Commands

| Command | Purpose |
| --- | --- |
| `summarize FILE` / `-` | summarize a file or stdin |
| `summarize doctor` | installation, configuration, engine, model, capability, and cache diagnostics |
| `summarize models` | models the configured local backend reports |
| `summarize profiles` | installed profiles, their source, and prompt identity (`--names`, `--format json`) |
| `summarize config show` | resolved configuration with sources; credentials redacted |
| `summarize config path` | config, prompt, session, and cache locations |
| `summarize config validate` | validate configuration and print the resolved engine |
| `summarize cache status\|path\|clear` | inspect and clear the optional cache |
| `summarize evaluate` | local model/backend evaluation harness (see [evaluation.md](evaluation.md)) |
| `summarize session start\|end\|status` | named run sessions (see [sessions.md](sessions.md)) |
| `summarize completions bash\|zsh\|fish` | shell completion script |

`summarize -` explicitly reads stdin. With no input argument and a TTY stdin,
`summarize` reports an error instead of waiting forever.

## Model and configuration discovery

```sh
summarize doctor              # layers, reachability, model, context, capabilities
summarize models              # models the configured local backend reports
summarize models --format json
summarize config show         # every resolved value and the layer that set it
```

`doctor` shows where each engine setting came from, whether the configured
model exists, which models are installed (when the backend can enumerate them),
the context window and its provenance, and the negotiated capabilities. It
distinguishes healthy (`✓`), optional/unavailable (`!`), and required failures
(`✗`). `models` reports what the backend offers; it selects nothing for you and
downloads nothing. If the configured model is absent you get the installed list
and a hint.

`config show` attributes each value to its configuration layer. It redacts
endpoint credentials and does not read environment values.

## Verifying a summary

`--verify` runs a mechanical check of the summary against the source after
generation: every number and every `ACRONYM (expansion)` phrase in the summary
that does not occur in the source is flagged. It is grep, not a judge model, and
a flag is for review rather than proof.

```sh
summarize paper.pdf --verify
summarize paper.pdf --verify --format json   # flags under "verification"
```

It catches fabricated numbers and invented expansions (for example an acronym
expanded to a phrase the paper never uses). It does not catch a real number
attached to the wrong label — both numbers are genuinely in the source — and an
extraction artifact can produce a false positive.

## Shell completions

```sh
summarize completions bash > ~/.local/share/bash-completion/completions/summarize
summarize completions zsh  > "${fpath[1]}/_summarize"
summarize completions fish > ~/.config/fish/completions/summarize.fish
```

Completions cover commands, options, formats, chunk strategies, backends, and
profiles (resolved dynamically via `summarize profiles --names`). No completion
framework is required.

## Exit status

| Code | Meaning |
| ---: | --- |
| 0 | success |
| 1 | input or usage error; `evaluate` with failing cases |
| 2 | local engine error |
| 3 | configuration error |
| 4 | explicit update-check error |
| 130 | interrupted with Ctrl-C |
| 141 | downstream pipe closed (e.g. `summarize file \| head`) |

`--check-update` is the only update-related operation. It is explicit,
check-only, and requires a configured update URL; normal summarization does not
check for updates or contact the network. `summarize evaluate` returns 1 when a
case's mechanical checks fail and 2 when the local engine is unreachable.
