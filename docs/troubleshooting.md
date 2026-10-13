# Troubleshooting

The two most common problems — a backend that is not reachable and a model that
is not installed — are in the [README](../README.md#troubleshooting). The rest
of the first support requests are here.

## The summary is empty, or "PDF text extraction failed"

The PDF is probably a scan with no text layer. Install the `ocr` extra plus the
system `tesseract` and Poppler binaries and pass `--ocr`; `summarize doctor`
reports whether both are present. The same message for a plain text file
usually means the file is not text (see "appears to be binary" below).

## `summarize` hangs or times out

Three different waits are bounded (`docs/performance.md` has the full model):

| Phase | Bound | Key |
| --- | --- | --- |
| Connect and response headers | `min(timeout_seconds, 5s)` | `[engine] timeout_seconds` |
| Non-streaming: the whole request | total `timeout_seconds` | `[engine] timeout_seconds` (`--timeout`) |
| Streaming: before the first byte | `first_token_timeout_seconds` | `[engine] first_token_timeout_seconds` |
| Streaming: after the first byte | idle, reset per byte, `timeout_seconds` | `[engine] timeout_seconds` |

A CPU-bound model can legitimately spend minutes processing the prompt before
its first token, so raise `first_token_timeout_seconds` (not the idle timeout)
if a streamed run is cut off before it starts. A non-streaming run is bounded
by the total `timeout_seconds`; raise it with `--timeout` for a slow machine.

## "exceeds the model's context window"

The token estimator under-counted for your tokenizer. The run retries with
smaller chunks automatically; if it still fails, raise the model's context
window, lower `[engine] max_tokens`, or set `[chunking] token_safety_margin`
above 1.0. `[engine] context_length` overrides discovery entirely.

## "the backend processed ... prompt tokens ... truncated" (or `"truncated": true`)

The server processed fewer prompt tokens than were sent, so the summary may be
missing the document's beginning. This is the guard against a backend that
silently drops the start of an over-long prompt. The run already retried with
smaller chunks and then accepted the result; fix it by lowering
`[chunking] max_tokens_per_chunk`, raising the model's context window, or
setting `[engine] context_length`.

While **streaming**, the tool can only warn — the text has already been printed,
so a retry would duplicate it. Use `--no-stream` to get the retry behavior. The
check needs a trustworthy serving window: when `summarize` had to guess the
window (a failed warm-up), it says so, because the check is weaker then. It is
also skipped for generic OpenAI-compatible backends, whose token counts vary.

## "appears to be binary, not text"

The file is not text, or it is a PDF without a `.pdf` extension (the reader
picks by extension). Convert it to text, or rename it to `*.pdf`.

## No input, or a wait on a terminal

`summarize` with no argument and an interactive terminal errors instead of
waiting for input. Pass a file, or `-` to read stdin explicitly.
