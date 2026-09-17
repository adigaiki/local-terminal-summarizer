# Security Policy / Threat Model

## Trust boundaries

Document content is untrusted. For every request it is wrapped in a fresh, cryptographically random boundary (`secrets.token_hex(12)`), separate from trusted instructions; map-reduce interim summaries get the same treatment with per-round boundaries, so instruction-like text in a document or an interim summary is treated as data, not as commands. `--context FILE` is the only user-supplied *trusted* extra input and gets its own labelled section.

## Local-only core

There is no cloud API, telemetry, analytics, or update channel in normal operation. The engine endpoint defaults to `http://localhost:11434`; configure your own local endpoint/model. `--check-update` is the only opt-in network operation and requires you to set a URL yourself; without it the tool performs no update traffic.

## Secrets & configuration

Keep configuration outside Git (`~/.config/summarizer/config.toml`, `./summarizer.toml`); `config.example.toml` is the only committed example and contains placeholders only. Never commit API keys or tokens; the project ships none. Machine-specific paths, personal prompt overrides (`/prompts/`), logs, caches, virtualenvs, and editor state are gitignored.

## Reporting

For vulnerabilities, open a private security advisory (GitHub "Security" tab) rather than a public issue.

## Known limitations (v0.2)

- Prompt-injection resistance depends on the model honouring the boundary contract; the boundary raises the bar but cannot make a probabilistic system a guarantee.
- Token counts are estimates, not a tokenizer; chunk sizes are approximate.
- PDF/OCR readers are optional extras with their own supply-chain surface.
