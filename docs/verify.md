# Mechanical claim verification (`--verify`)

`--verify` checks a summary against the source text it was generated from and
reports claims that do not appear there. It is a **grep, not a judge model**: it
extracts two cheap, checkable claim types and looks for them in the source.

- **Numbers** — a numeric token in the summary that does not occur in the
  source text. Rounding is tolerated, because `1.60` is a substring of
  `1.6004`. Numbers too short to be meaningful (one or two digits with no
  decimal point) are skipped.
- **Acronym expansions** — an `ACRONYM (some phrase)` whose expansion phrase
  does not occur in the source. This catches invented expansions such as
  `M.E.W. (Molecular Ensemble with Water)` even when `M.E.W.` itself is real
  and appears throughout the document.

## What you see

The check runs after the summary and writes a report to **stderr**; stdout stays
clean and composable, and the summary is never rewritten. It also works with
`--format json`.

```console
$ summarize paper.pdf --verify
<the summary appears on stdout>
verification: no unverified claims (34 numbers, 0 expansions checked)
```

```console
$ summarize paper.pdf --verify
<the summary appears on stdout>
verification: 2 unverified claim(s) (34 numbers, 1 expansions checked)
  number: 89.42(2)
  expansion: M.E.W. (Molecular Ensemble with Water)
```

With `--format json`, the same data is a top-level `verification` object in the
envelope — present even when nothing is flagged:

```json
"verification": {
  "numbers_checked": 34,
  "expansions_checked": 1,
  "unverified": [
    {"kind": "number", "text": "89.42(2)"},
    {"kind": "expansion", "text": "M.E.W. (Molecular Ensemble with Water)"}
  ]
}
```

`--verify` is **advisory**: it does not change the exit status and does not stop
or edit the summary. A flag is for your review, not a machine verdict.
`--stats` is unrelated and reports timing/token counts on stderr.

## What it does not catch

- **A real number on the wrong label.** If a table reports `a = 1297.06` when
  `1297.06` is actually the volume, both values are in the source, so neither is
  flagged. Structured table extraction
  ([readers.md](readers.md#tables-in-pdfs)) addresses misassignment; `--verify`
  addresses fabrication. They are complementary.
- **`Phrase (ACRONYM)` order.** It matches `ACRONYM (expansion)`. A model that
  writes `Mechanical Equivalent of Water (M.E.W.)` is not flagged, because the
  phrase precedes the acronym.
- **Broken extraction spacing.** If the source renders a value as `1153. 9(4)`,
  a summary that writes `1153.9(4)` looks absent and is flagged — a false
  positive.
- **Computed or paraphrased values.** A percentage derived from two source
  numbers legitimately does not appear verbatim.
- **Non-numeric, non-acronym fabrication** — invented names, methods, and causal
  claims.

A clean report is therefore not proof of faithfulness, and a flag is not proof
of error; it is a cheap first pass. The benchmark that measures whether the
summaries are actually good — and shows `--verify` catching a genuine
fabricated number (`89.42(2)`) while missing a column swap — is
[quality.md](quality.md), and the mechanical evaluation harness is
[evaluation.md](evaluation.md).
