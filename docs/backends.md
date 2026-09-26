# Backends and capability negotiation

Backends are adapters behind one generic interface — `generate()`, `stream()`,
`health()`, `list_models()`, and `capabilities()`. The pipeline does not branch
on a backend name; it asks the backend what it supports and negotiates:

| Capability | Meaning |
| --- | --- |
| `supports_streaming` | live token streaming is available |
| `supports_structured_output` | JSON response format is advertised (three-valued: yes/no/unknown) |
| `supports_model_listing` | the endpoint can enumerate installed models |
| `supports_reasoning_control` | the endpoint accepts a reasoning budget |
| `context_length` (+ source) | the window to budget against, and how it was learned |

Implemented adapters:

| `backend` | Kind | Notes |
| --- | --- | --- |
| `ollama` | native + OpenAI-compatible | discovery via `/api/ps`, `/api/show`, `/api/tags` |
| `openai-compatible` (alias `openai`) | generic | any local OpenAI Chat Completions server |
| `llama.cpp` (alias `llama-cpp`) | generic | default endpoint `http://localhost:8080` |
| `lmstudio` (alias `lm-studio`) | generic | default endpoint `http://localhost:1234` |

Backends with no declared endpoint keep the configured one. If you never set
an endpoint, selecting a backend uses its documented loopback default.

Adding an OpenAI-compatible local runtime is a catalogue entry in
`src/summarizer/engine/backends.py`; a runtime with behaviour the generic API
cannot express gets one small adapter class. There are no cloud providers.

## How negotiation shows up

- Streaming is used only when you asked for it and the backend reports support;
  otherwise the run falls back to a single request.
- `--format json` requests a structured object when the backend advertises it,
  and validates the reply strictly regardless (see [readers.md](readers.md)).
- `reasoning_effort` is sent only to backends that advertise reasoning control;
  otherwise it is omitted with a verbose warning.
- Context length is discovered from the local server when possible, then from
  `[engine] context_length`, then from a conservative fallback. The source is
  reported by `summarize doctor` and `--dry-run`.
