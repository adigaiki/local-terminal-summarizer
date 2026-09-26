# Readers, provenance, and limits

Every reader produces the same `Document` object — content, source, MIME type,
encoding, size, and reader metadata — so the backend and chunker do not know
which reader ran.

| Input | Reader | Notes |
| --- | --- | --- |
| `-` / no argument | stdin | Bounded; a TTY with no input reports an error |
| `.txt`, `.log`, other/unknown extensions | text | Any file whose content is valid text stays usable |
| `.md`, `.markdown`, `.mdown`, `.mkd` | markdown | Source is preserved verbatim; headings are collected |
| `.py`, `.rs`, `.c`, `.go`, `.toml`, ... | code | Source passes through unmodified; language is recorded |
| `.pdf` | PDF (optional) | Page-aware extraction with page provenance |

Markdown is not rendered to HTML, link destinations are not fetched, source
code is not parsed or executed, and no reader treats document text as
instructions. Content containing NUL bytes is rejected with a clear error
rather than summarized as mojibake.

## Optional dependencies

```sh
pip install 'summarizer[pdf]'   # PDF text extraction (pypdf + fonttools, both BSD-3-Clause)
pip install 'summarizer[ocr]'   # OCR: adds pytesseract + pdf2image
```

PDF reading is entirely local: nothing is downloaded and no PDF metadata is
sent anywhere. Encrypted PDFs are refused rather than worked around. Scanned
(image-only) PDFs are detected and reported instead of silently producing an
empty summary. `--ocr` reads them locally and additionally needs the system
`tesseract` and Poppler (`pdftoppm`, `pdfinfo`) binaries; missing components
produce an actionable error. `summarize doctor` reports both capabilities,
marking an intentionally uninstalled extra with `!` rather than a failure.

## Tables in PDFs

A PDF text extractor flattens a table into rows of bare values, with the column
labels appearing once in a header line. A small model then has to carry that
mapping across every row, and it frequently does not. The reader therefore
detects simple header-plus-rows tables and rewrites each row with explicit
labels before the text ever reaches the prompt:

```text
P (GPa): Pamb | a (Å): 9.6622(7) | b (Å): 13.6819(10) | c (Å): 9.8116(11) | V (Å 3): 1297.06(19)
```

The transform is conservative — it fires only on a header line with two or more
`label (unit)` tokens followed by at least two mostly-numeric rows of matching
width — and non-table text passes through unchanged. It can be disabled with
`[input] structure_tables = false`. When it fires, the count is reported in the
JSON metadata as `structured_tables`. It reduces column-mapping errors but does
not make a small model reliable on wide tables.

## Provenance and the JSON schema

Chunks carry provenance: source, chunk index and count, character offsets,
source line range, PDF page range (when the input has pages), the boundary that
produced the cut, and the overlap size. Line and page numbers are one-based and
inclusive; character offsets are zero-based, half-open Python string offsets.
`--format json` emits a stable envelope:

```json
{
  "document": {"source": "...", "mime_type": "...", "encoding": "...",
               "size_bytes": 0, "line_count": 0, "char_count": 0,
               "metadata": {"pages": 0, "page_starts": [], "warnings": []}},
  "profile": "plain",
  "engine": {"backend": "ollama", "model": "...", "endpoint": "..."},
  "strategy": "direct|mapreduce",
  "chunks": 0,
  "summary": {},
  "warnings": [],
  "chunk_provenance": [
    {"index": 0, "count": 0, "source": "...", "boundary": "paragraph",
     "start_char": 0, "end_char": 0, "start_line": 1, "end_line": 1,
     "start_page": null, "end_page": null, "chars": 0, "est_tokens": 0,
     "overlap_chars": 0}
  ],
  "prompt_boundary": "document-...",
  "duration_seconds": 0.0
}
```

`metadata` and `chunk_provenance` are omitted when empty. Provenance appears in
JSON and `--dry-run` output only, and is not inserted into trusted prompt
instructions.

## Resource limits

Rich formats add parsing surface, so readers are bounded by `input.max_bytes`,
`input.max_lines`, and, for PDFs, `input.max_pdf_pages` and
`input.max_extracted_bytes`. Defaults are generous for real research
documents; absurd or hostile input fails with a clear error rather than
exhausting memory. The token estimator used for chunk sizing is an estimate,
not a tokenizer.

Oversized input is budgeted against the model's *discovered* context window
(via the local server's metadata when it can be read, otherwise your configured
`context_length`, otherwise a conservative fallback), then chunked at
paragraph/sentence/word boundaries with overlap and summarized via map-reduce.
Use `--chunk-strategy tokens|chars` to pick the sizing unit, and `--strict` to
refuse chunking entirely instead of degrading to map-reduce. `--dry-run` reads
and plans the request without probing or contacting a backend.
