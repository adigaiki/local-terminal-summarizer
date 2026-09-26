# Summary quality: the honest current state

The project has no objective quality score. It has one small, **manual**
benchmark, recorded below: a handful of documents scored for coverage against
human-written reference claims, run on two local models. That is a smoke
signal, not a verdict, and no number here should be read as an objective
quality score.

The evaluation harness ([evaluation.md](evaluation.md)) checks that output is
well-formed and structurally safe: valid format, valid JSON, a retained
sentinel, no echoed injection marker, expected strategy, bounded size. A summary
can pass all of those and still omit the point, invert a claim, or invent a
fact. The claims pass below is the first attempt to look past those mechanical
checks; it is deliberately small and its caveats are stated throughout.

This page records the gap, the method, and the first observations. The
ordering matters more than the mechanism: run it once by hand, read the
failures, then automate only what proved worth automating.

## Why this is the next real milestone

Every milestone from v0.4 onward added reliability and workflow: sessions,
caching, concurrency, cancellation, packaging. Those are plausible things to
build, but they were built without evidence that anyone needed them, because
the core question — are the summaries any good? — was never answered. More
infrastructure will not answer it.

## The benchmark to build

Small and unglamorous:

1. **A handful of documents** (say 8–12) spanning the built-in profiles: a
   short news article, a code file, an academic abstract, meeting notes, a log
   excerpt, a bilingual note. Small enough to run repeatedly.
2. **Human-written reference summaries** for each, as a list of *atomic
   claims* rather than prose, so scoring is mechanical and reviewable. Example:
   `["the cache node failed", "the API served ~4% of requests from a stale
   replica", "rollback happened at 03:05", "no customer data was lost"]`.
3. **Two metrics, reported separately, never combined into one score**:
   - **Coverage** — the fraction of reference claims the summary supports.
   - **Omission** — the reference claims that are missing.
   Optionally a third, **unsupported claims** — statements in the summary not
   in the reference, flagged for human review rather than scored automatically.
4. **Two models**, so the number means something relative: for example
   `qwen3:8b` and a second local model.
5. **Report the raw per-document results** and the model comparison. No single
   headline number, and no claim of objectivity: coverage against a small
   human reference is a smoke signal, not ground truth.

This is a day of work, not a milestone, and it directly addresses the gap the
other six versions did not.

## Running the first pass by hand

Do this manually. Do not build a harness first — run it once, read the
failures, then automate only what proved worth automating.

**0. One-time setup**

- Real papers need a longer timeout than the 60s default. Either pass
  `--timeout 300` per run, or set it once in `~/.config/summarizer/config.toml`:
  ```toml
  [engine]
  timeout_seconds = 300
  retries = 1
  ```
- A second local model is required for the comparison, e.g.
  `ollama pull llama3.2:3b` (about 2 GB). Note the tradeoff: if you choose a
  smaller model for speed, the comparison becomes **8B-vs-3B**, which is a
  different and less clean question than 8B-vs-8B. That is fine and still
  informative, but say so in the results — otherwise a later reader assumes
  model size was controlled when it was traded for tractability.

**1. Pick 8–12 real documents** (no synthetic text), one per slot:

| Slot | Suggested real source |
| --- | --- |
| 3–4 papers (PDF) | real papers you already have locally — the benchmark used Brown_1969, Evans_1973, Danisi_2015, Ottens_2025 |
| 1 source file | `src/summarizer/pipeline.py` (`--profile code`) |
| 1 real diff | `git -C <your-checkout> diff > /tmp/bench/pr.diff` |
| 1 log excerpt | any real server/service log you have locally |
| 1 meeting/notes file | your own; if you have none, use a real doc you would actually summarize |
| 1 short news/misc text | paste a real article into a file yourself (the tool fetches nothing) |
| 1 bilingual/short text | optional, if you have one |

Check each PDF before committing to it:

```sh
summarize "<paper>.pdf" --dry-run
```

Two real gotchas, both seen on these files:

- A **scanned** PDF extracts almost nothing (Bagley_1969 → 62 chars). Skip it,
  or install the OCR extra; do not score a paper the reader could not read.
- A PDF **without a `.pdf` extension** is treated as text and rejected
  ("appears to be binary"). Copy/rename it to `*.pdf` first.

**2. Write atomic claims cold, from the source only.** For each document,
before running any model, write a file `benchmark/claims/<slug>.md` with 5–15
bullets. Each bullet is one verifiable claim, not prose. Keep numbers, names,
and polarity exact ("bond length increases with coordination", not "bond
length changes"). Do not look at a summary while writing these; grading a
summary against itself is the failure mode this step exists to prevent.

**3. Run both models, one clean pass each.** No prompt tuning, no retries by
hand, no cherry-picking. Capture raw output to files:

```sh
for m in qwen3:8b llama3.2:3b; do
  for f in /tmp/bench/docs/*; do
    slug=$(basename "$f")
    .venv/bin/summarize "$f" --profile plain --model "$m" \
      --no-stream --no-progress --timeout 300 \
      > "/tmp/bench/out/$m-$slug.md" 2>&1
  done
done
```

Use `--profile academic` for papers, `--profile code` for source/diffs,
`--profile meeting` for notes.

**4. Score by hand.** For each document and model, read the summary against
the claims and record:

- **coverage** = supported claims / total claims
- **omission** = the list of claims that are missing
- **unsupported** = statements in the summary not in the reference (flag for
  review; do not fold into a score)

**5. Write it into this file, unfiltered.** Per-document table plus raw notes.
The most valuable sentence in the whole exercise is an invented fact or a
flipped claim, so include it verbatim.

**6. Decide from the result, not before.** Solid coverage and minor omissions →
ship v0.6 and get users. A systematic failure (drops numbers, mangles
multi-step instructions) → that specific failure is v0.7.

## Before you run: two settings decide direct vs map-reduce

This bit the first run, so check it before the benchmark or you will score
map-reduce quality on documents that never needed map-reduce.

`chunking_decision` uses `max_chunk = min(context − prompt − output,
[chunking] max_tokens_per_chunk)`. The default `max_tokens_per_chunk` is
**3000**, so any document over ~3k tokens is chunked *regardless of the
context window*. Measured on the Brown_1969 paper (~6,787 tokens):

| `max_tokens_per_chunk` | context | chunks | single call |
| ---: | ---: | ---: | --- |
| 3000 (default) | 16384 | 3 | no |
| 12000 | 16384 | 1 | yes |
| 3000 (default) | 40960 | 3 | no |

Two things must be raised together, and raising only one changes nothing:

1. **`[chunking] max_tokens_per_chunk`** — the binding constraint. Set it high
   enough that a document that fits the context goes in one direct call, or
   make a deliberate decision to benchmark map-reduce.
2. **Ollama's `num_ctx`** — Ollama serves a default window of 4096 unless the
   model sets it. Check `ollama ps` (the `CONTEXT` column) or create a model
   with `PARAMETER num_ctx 16384` (see below).

A third setting decides how long a run takes. **`[engine] max_tokens`** caps
generated tokens for *every* call. On CPU-heavy local setups, generation is far
slower per token than prompt processing, so a 1024-token cap multiplied across
five calls dominates the runtime. Lowering it speeds a run up — but 512 is *not*
enough for the academic profile: it truncated the summary mid-sentence in the
direct test above. Keep 1024 for scoring; treat 512 as a timing-only setting.

The budget interacts with the window. With a 1024-token output budget the
Brown_1969 paper no longer fits an 8192 context (`usable = 8192 − prompt(~460)
− 1024 ≈ 6708`, just under the paper's ~6787 tokens), so it would chunk into 2.
To keep a document both complete and direct, raise the context and the output
budget together (12k or 16k here), or accept that the largest documents will
chunk.

`summarize doctor` reports the model's *trained* maximum (40960) when the model
is unloaded and the *runtime* serving window when it is loaded, so its answer is
**state-dependent**. Configure against the unloaded number and the budget shrinks
as soon as the model actually loads. Trust `ollama ps` (the `CONTEXT` column),
or load the model first, before deciding.

```sh
# a derived model with a larger serving window
cat > Modelfile <<'EOF'
FROM qwen3:8b
PARAMETER num_ctx 16384
EOF
ollama create qwen3-8b-16k -f Modelfile
```

For the benchmark, keep both settings identical across models so the comparison
is about summarization, not about chunking.

## First observation (unscored)

A smoke run on a real paper (`Brown_1969`, 12 pages, `--profile academic`,
`qwen3:8b`, default settings):

```text
chunks: 3   map: 290.3s   reduce: 85.8s   total: 376.3s
generated: ~2053 tokens (estimated)
```

The output retained specific values — Si–O bond lengths 1.607–1.651 Å,
coordination numbers 2.0–4.0, the Allred–Rochow and Pauling scales. This is a
single unscored run, not evidence; it is recorded only so the first real data
point exists before the claims were written.

### Direct vs map-reduce on the same paper (confirmed)

| settings | chunks | time | output |
| --- | ---: | ---: | --- |
| default chunk cap 3000, context 4096, `max_tokens` 1024 | 3 | 17m54s | complete |
| `max_tokens_per_chunk` 7200, context 8192, `max_tokens` 512 | 1 | 2m15s | **truncated mid-sentence** |
| `max_tokens_per_chunk` 12000, context 16384, `max_tokens` 1024 | 1 | 7m20s | complete (669 tokens) |

So the chunk cap and the output budget, not the model, were the cost. The
512-token cap truncated the summary (the backend reported exactly 512 output
tokens; the text ends "…thermal effects in alpha"). The speed win there was
partly "generated less text". Keep `max_tokens` at 1024 for scoring; use 512
only for timing checks. Note also that 16k context was much slower per token
(~1.5 tok/s) than 8k (~3.8 tok/s) on this machine — the larger KV cache has a
cost, so use the smallest window that keeps documents direct.

### Candidate polarity issue — did not reproduce

The 512-truncated summary said: *"increasing electronegativity of the
nontetrahedral cations leads to a shortening of Si-O bonds, which is contrary to
some earlier assumptions."*

On the complete 1024 run it says the opposite of that, and matches the paper:
*"Longer Si-O(nbr) bonds are associated with more electronegative cations"* —
and it preserves the two-mechanism structure (the d-p π-bonding exception, where
Si-O(nbr) bonds to three- and four-coordinated oxygens in tremolite can be
shorter than Si-O(br)). So the candidate was a **truncation artifact, not a
stable failure**. Recorded anyway: it is exactly the kind of claim that must be
re-tested on an untruncated run before it is believed, and a source with a
general-trend-plus-exception structure remains a good stress test for the
scored pass.

Two clean runs (qwen3 and qwen2.5) is good evidence, not settled evidence: a
polarity-shaped error was observed earlier on a truncated qwen3 run. The claims
pass, not this note, carries the verdict — do not let "0 for 2" harden into
"solved."


## Two-model read (unscored)

Settings for all four runs: context 16384, `max_tokens` 1024,
`max_tokens_per_chunk` 12000. Every run was **1 chunk, direct** — no
map-reduce.

| document | model | time | output tokens | complete? |
| --- | --- | ---: | ---: | --- |
| Brown_1969 (`academic`) | qwen3-8b-16k | 7m20s | 669 | yes |
| Brown_1969 (`academic`) | qwen2.5-3b-16k | 37s | 1024 (cap) | **no — truncated** |
| mapreduce.py (`code`) | qwen3-8b-16k | 3m55s | 960 | yes |
| mapreduce.py (`code`) | qwen2.5-3b-16k | 27s | 1009 | yes |

Read, not scored:

- **qwen3-8b** was complete and faithful on both. The polarity claim is
  correct ("longer Si-O(nbr) bonds are associated with the more electronegative
  cations"), and the code summary named real methods and invariants
  (`_run_map_parallel`, `on_result`/`cached`, `_bound_oversized`, boundary
  wrapping, lossy truncation).
- **qwen2.5-3b** is ~6–9× faster. Its code summary is complete and mostly
  accurate, but it asserted *"the `Chunk` class also contains methods for
  estimating the number of characters and tokens in a chunk"* — false;
  `token_estimate`/`char_count` are fields and splitting is `split_document`.
  Candidate hallucination, to confirm in the claims pass.
- **qwen2.5-3b truncated the academic summary** at the 1024-token cap (backend
  reported exactly 1024) and reads more extractive — it reproduces the journal
  header and authors. A fixed output cap can penalise the more verbose model
  through truncation rather than content. For a fair scored pass, give enough
  budget for the most verbose model, or score only complete outputs.

No scoring yet; these are the observations the claims pass should test.

### Worked example: a wrong claim that passes every mechanical check

qwen2.5-3b's summary of `mapreduce.py` asserts:

> "The `Chunk` class also contains methods for estimating the number of
> characters and tokens in a chunk."

It does not. `Chunk` is a frozen dataclass whose `token_estimate` and
`char_count` are fields; chunk splitting lives in `split_document` in
`chunking/splitter.py`. The sentence is confident, specific, and plausible — and
it passes every check the evaluation harness runs (valid format, valid JSON,
bounded size, sentinel retention). Mechanical checks confirm the output is
*well-formed*, not that it is *true*. That gap is the entire reason for this
benchmark, and this sentence is the cleanest illustration of it so far.

## Claims pass (first scored run)

Claims are in `benchmark/claims/`: `brown-1969.md`, `mapreduce.md`,
`evans-1973.md` (written after their outputs were seen — not a clean cold-start
pass), and `danisi-2015.md` (**written from the full text before the run —
cold**). The Danisi set is the fix for the claim-set-narrowness problem: claims
now cover abstract, methods, results, and discussion, not just the abstract.

### Document set status (honest)

| slot | status |
| --- | --- |
| Brown_1969 (academic) | done, scored |
| Evans_1973 (academic) | done, scored |
| Danisi_2015 (academic) | qwen2.5 done (map-reduce, scored); qwen3 **timed out** |
| Ottens_2025 (academic) | available but ~31k tokens — would map-reduce heavily; not yet run |
| `mapreduce.py` (code) | done, scored |
| a real git diff (code) | not yet run |
| log | **not tested** — no suitable real log sample. Real build logs exist (`Ray_Optics_Test_Paper.log`, a `config.log`) but were not used; none is a server/service log |
| meeting notes | **not tested** — only a 123-byte template (`Full Notes.md`) exists, no real notes |
| news article | **not tested** — no real article file; not manufactured |

Two categories are marked untested rather than padded with synthetic filler.
An 8–10 document benchmark with honest gaps is worth more than a 12-document one
with invented ground truth.


**Cap decision: 2048, uniformly.** 1024 truncated qwen2.5 on the academic paper;
2048 also truncated it (1,059 words and still unfinished). So the cap is set to
2048 for the remaining documents, and **qwen2.5 on Brown_1969 is recorded as
"not scored, capped"** rather than a loss. Its inability to produce a concise
summary of dense academic prose is itself the finding — a summary that needs
more than 2,048 tokens for a 12-page paper is not doing the job.

Scoring is manual, per claim: **supported / partial / omitted / false**. The
length ratio is a third, purely mechanical signal (backend-reported output
tokens ÷ the tool's input-token estimate), reported next to coverage and never
merged into it.

| output | supported | partial | omitted | false | coverage | length ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Brown_1969 — qwen3-8b | 6 | 1 | 5 | 0 | 6/12 (50%) | 669/6787 = 9.9% |
| Brown_1969 — qwen2.5-3b | — | — | — | — | **not scored (capped)** | 2048/6787 = 30.2% (capped) |
| mapreduce.py — qwen3-8b | 9 | 2 | 1 | 0 | 9/12 (75%) | 960/3659 = 26.2% |
| mapreduce.py — qwen2.5-3b | 7 | 1 | 3 | **1** | 7/12 (58%) | 1009/3659 = 27.6% |
| Evans_1973 — qwen3-8b | 4 | 4 | 4 | 0 | 4/12 (33%) | 1095/10872 = 10.1% |
| Evans_1973 — qwen2.5-3b | 3 | 3 | 6 | 0 | 3/12 (25%) | 386/10872 = 3.6% |
| Danisi_2015 — qwen3-8b | — | — | — | — | **not scored (timeout, no output)** | — |
| Danisi_2015 — qwen2.5-3b | 0 | 1 | 13 | **4** | 0/18 (0%) | 1976/13537 = 14.6% (map-reduce, 2 chunks) |
| Danisi_2015 — qwen2.5-3b (fixed pipeline) | 1 | 2 | 12 | **3** | 1/18 (6%) | 2048/13964 (map-reduce, 2 chunks) |

### Danisi before/after the pipeline fixes (same claims, same settings)

Same model, same 2-chunk map-reduce shape, same 18 cold claims; only the input
treatment changed (structured tables) plus `--verify`.

| | supported | partial | omitted | false | coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| before (flat table text) | 0 | 1 | 13 | 4 | 0/18 |
| after (structured tables + verify) | 1 | 2 | 12 | 3 | 1/18 |

**The coverage score did not move.** That is the real finding, and it separates
two things cleanly:

- **What the pipeline fix repaired (input quality):** the reproduced table is
  now *correct*. Before, `c` was reported as `1297.06` (the volume) and the
  pressure/lattice columns were shifted; after, the cavansite row is
  `Pamb | 9.6622(7) | 13.6819(10) | 9.8116(11) | 1297.06(19)` — exact. The
  column-mapping failure is gone.
- **What it could not repair (model ceiling):** the summary still *reproduces
  tables instead of summarizing findings*. It still invents acronym expansions —
  two more, different again ("Mechanical Equivalent of Water", "Solvent") — and
  it duplicated the cavansite table under "Pentagonite". None of the narrative
  findings (anisotropy, transitions, over-hydration, E₀S values, ring
  topologies) appear.

`--verify` did its job: 3 flags — 2 genuine invented expansions and 1 false
positive (`1153.9(4)`, which is in the source only as the broken token
`1153. 9(4)`; the source text spacing defeats the check). Coverage is
unimproved; fabrication detection and table fidelity are.

### First map-reduce sample: a confidently wrong table

Danisi_2015 was the first document to trigger map-reduce (2 chunks, qwen2.5).
The result is the strongest evidence yet that mechanical checks miss
correctness. It is well-formatted markdown with tables and looks authoritative,
and it is largely wrong:

- It **mis-parsed the extracted table**, swapping columns: `a` is reported as
  `13.6819(10) at 9.6622(7) GPa` — but `13.6819` is the `a` value and `9.6622`
  is also a lattice value, not a pressure. It reports `c (Å): 1297.06(19)`
  (that is the **volume**, in Å³), and `α (°): 1315.9(2)` (impossible).
- It **invented acronym expansions**: "M.E.W. (Molecular Ensemble with Water)"
  and "S.O. (Single-Organic)". Neither phrase occurs in the source; m.e.w. is
  methanol:ethanol:water and s.o. is silicone oil.
- It omitted essentially every finding: anisotropy (1.6/10.3/0.3%), T–O–T
  driver, over-hydration and stiffening, the orthorhombic→triclinic transitions
  (2.45–2.96 GPa; >1.71 GPa), the equation-of-state values
  (V₀ = 1300(1) Å³, K₀ = 38.1(5) GPa), the Pnma/Ccm2₁ settings, and the ring
  topologies.

Confounds, stated plainly: this is a 3B model, on the map-reduce path, over a
PDF whose extraction was degraded (pypdf warned `fontTools` is missing, and the
tables came out as flat value sequences). So this is not "map-reduce is bad" —
it is one bad sample with three plausible causes. But the failure *shape* — a
tidy, confident, numerically garbled summary that passes every automated check —
is exactly the one this benchmark exists to expose.

### Danisi / qwen3: timeout, no output

qwen3-8b did not finish Danisi in 75 minutes and produced nothing. At 16k
context it runs ~1.5 tok/s, and a 13.5k-token paper at `max_tokens` 2048 with
map-reduce is more than this machine can complete in a sane window. Operational
limit, not a quality result: **not scored.**


Raw outputs are kept under `benchmark/results/` (gitignored — derived summaries
of private documents must not be committed).

### Verbosity is document-dependent, not a model trait

qwen2.5 was **30% and unfinished** on Brown_1969 but **3.6% and complete** on
Evans_1973 — the same model, two academic papers, opposite shapes. So the length
ratio has to be read per document; it is not a stable property of a model. This
is also why a single model-level "verbosity" judgment would be wrong.

### Claim-set dependence (a real limitation)

qwen3's Evans summary scored only 33% against these claims, but spot-checking
roughly fifteen of its extra specifics (`redstarite`, Si–O 1.627/1.6004 Å,
V valence sums 4.33/4.46, tetrahedral angles 106.8°/112.0°, "one-dimensional in
pentagonite", "concentric prismatic tubes") found **all of them in the source**.
The low coverage reflects the claim set (abstract-level framing: locality,
space group, novelty, mirror-plane placement, R values) more than the summary's
fidelity. Coverage is only as good as the claims, and a small abstract-derived
set under-counts a detailed-but-unfocused summary. Worth remembering before
treating 33% as a quality number.

### Triage: the Danisi table errors are the model, not pypdf

Installed `fontTools` and re-extracted Danisi. The table region is
**byte-identical** before and after, and the header row
`P (GPa) a (Å) b (Å) c (Å) V (Å 3)` was already present without fontTools —
pypdf had not thrown the headers away. Re-running qwen2.5 on the fontTools
extraction produced **new** invented acronym expansions ("Multi-Element-Wedge" /
"Single-Element-Wedge"; the earlier run said "Molecular Ensemble with Water" /
"Single-Organic" — none appear in the source), while this time the hydrogen-bond
distances came out correct. The errors are nondeterministic model failures on a
labelled table, not a stable extraction defect. fontTools is still a reasonable
install for character decoding, but it is not the cause here.

### Hardware limit: 4 GB VRAM cannot hold qwen3:8b

The GPU is an RTX 2050 with **4096 MiB**. qwen3:8b is 5.2 GB, so it cannot fit;
Ollama splits it **65% CPU / 35% GPU** and it runs at ~1.2 tok/s. A smaller quant
of the same model does not fix this — the weights alone already exceed VRAM
(this install is already Q4_K_M). qwen3:4b (2.5 GB) fits better: 46/54 at 16k,
28/72 at 8k, ~18 tok/s — but it **leaked its chain-of-thought into the output**
on a one-sentence input (1,368 words of "We are given a source document…", hit
the cap). `reasoning_effort = "none"` did not suppress it through Ollama's
OpenAI-compatible endpoint.

Net: on this machine qwen2.5:3b is the only model that is both GPU-resident and
produces clean summaries. qwen3:8b is CPU-bound; qwen3:4b is faster but its
reasoning leak makes it unusable for scoring until that is fixed. Two candidate
v0.7 items fall out of this: reliable reasoning suppression (Ollama
`think: false` / the native API rather than the OpenAI-compat field), and having
`doctor` report the CPU/GPU offload split.

### Pipeline fixes tested (v0.7 candidates)

Two mechanical fixes were built and validated on Danisi — no model change.

**1. Structured table extraction** (`summarizer.document.tables`, on by default
via `[input] structure_tables`). Simple header-plus-rows tables are rewritten so
every value carries its own label:
`a (Å): 9.6622(7) | b (Å): 13.6819(10) | ...`. Validated on the real PDF: 2
tables structured, replacing the bare number stream. Effect on the run: input
grew ~10% (13,964 tokens), it chunked into 6, and the output rendered a table
*with headers* rather than shifting columns silently. Honest limit: it does not
fully fix a 3B model on a wide 8-column table — that run still produced rows
with the wrong number of cells.

**2. Mechanical claim verification** (`--verify`). Numbers and
`ACRONYM (expansion)` phrases in the summary are checked against the source and
unmatched ones are flagged — grep, not a judge. It earned its keep immediately:
it flagged `89.42(2)`, which does **not** appear in the Danisi source (which has
89.468(11), 89.46(2), …), i.e. a genuine fabricated number.

Two honest limits:

- It does **not** catch the `a`↔`P` column swap, because both numbers *do*
  exist in the source. Table structuring addresses misassignment; verification
  addresses fabrication. They are complementary, not redundant.
- It checks the *expansion* phrase, not the acronym: `M.E.W.` itself occurs 51
  times in the source, so checking the acronym would have caught nothing. The
  invented "Molecular Ensemble with Water" is what fails.

### Self-consistency as a benchmark signal

Run the same document twice and diff. The two pre-fix qwen2.5 Danisi runs
invented *different* fake expansions on identical input ("Molecular Ensemble
with Water", then "Multi-Element-Wedge" / "Single-Element-Wedge"). Instability
across runs is itself evidence of fabrication. This is a benchmark signal, not a
production feature — two runs and a diff, no code.

### Why a length ratio, and why it is not folded into coverage

Coverage alone cannot catch a model that maximizes coverage by staying close to
verbatim: "reproduce the whole paper" trivially scores high. A summary that is
80% of the input's length is not a summary regardless of how many reference
claims it contains. The ratio is checkable with no judgment call, so it sits
beside coverage/omission rather than inside it. The capped qwen2.5 row is the
shape to watch: high ratio, no completion.

### Faithful is not the same as complete

An earlier unscored read called qwen3 "complete and faithful on both"; the
scored pass shows **50% coverage** on Brown_1969. Both are true and they measure
different things:

- **Faithful** = nothing it said was wrong (zero false claims).
- **Coverage** = how much of what mattered it said at all.

A model can be zero-hallucination and still a mediocre summary because it drops
half the substance. qwen3's five omissions (method exclusions, the glaucophane
0.01 Å figure, the grant, the added-in-proof trend) are not errors — they are
absences. A single "quality score" would have hidden exactly this distinction,
which is why coverage, omissions, false claims, and length are reported
separately.

### A handful of documents is not a ranking

Four documents have produced several distinct failure shapes: a confidently
false API detail (qwen2.5/code), an inability to compress dense academic prose
(qwen2.5/Brown), a faithful-but-unfocused summary that missed the framing facts
(qwen3/Evans), and a confidently mislabeled numeric table with invented acronym
expansions (qwen2.5/Danisi, map-reduce). That is real signal. It is **not**
enough to rank the models: the numbers are a few data points from non-comparable
claim sets, with different models on different paths (direct vs map-reduce) and
at least one model/cap confound. Read each row as an observation, not a score.

What the omissions actually were:

- **qwen3 / Brown**: omitted the feldspar–zeolite and Na-silicate exclusions,
  the glaucophane "0.01 Å longer than tremolite" figure, the NSF grant, and the
  Morimoto–Koto added-in-proof trend. It kept the main results (1.608→1.638 Å,
  r = 0.78, the electronegativity correlation, the d-p π exception). For a
  concise summary that is a defensible trade; the benchmark records it, it does
  not judge it.
- **qwen3 / mapreduce.py**: omitted only the two magic constants
  (`REDUCE_SUMMARY_OVERHEAD_TOKENS = 24`, `MAX_REDUCE_ROUNDS = 8`) and was vague
  on lowest-index failure selection.
- **qwen2.5 / mapreduce.py**: omitted deterministic ordering and the
  error-handling detail, and made one **false** claim (the `Chunk` methods
  example above). It scored *lower than the 8B model despite being complete*.

Two things this first pass already shows:

1. **Mechanical checks and correctness are orthogonal.** qwen2.5's mapreduce
   summary would pass every check in the evaluation harness while containing a
   confidently wrong statement about the code.
2. **Coverage alone is not quality.** qwen2.5's academic output probably
   *covers* the most, because it paraphrases the entire paper — and it is the
   worst summary, because it never finishes. A future metric should treat
   "complete and concise" as a property in its own right.

Nothing here is settled: two documents, one manual pass, one truncated model,
and a claims set drafted after the outputs were seen. It is a smoke signal, not
a verdict.

## What would make it untrustworthy

- Scoring with a model judge and presenting its verdict as objective.
- A tiny or unrepresentative document set.
- Collapsing coverage, omission, and unsupported claims into one "quality
  score" that hides which failure happened.
- Reporting a percentage without the per-document results.

## Sequencing

1. Ship v0.6 and get a few real users.
2. Build the benchmark above; run it across two models.
3. Let the results, not the roadmap, decide what to build next.

Until step 2 exists, the correct statement about summary quality is: **unknown,
and not claimed.**
