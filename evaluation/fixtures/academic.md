# Local Search over Sparse Retrieval Indices

## Abstract

We study whether a small locally hosted language model can improve keyword
retrieval without a cloud service. Our system, named **quasar-42**, combines a
BM25 baseline with a lightweight re-ranker that runs entirely on the user's
machine. On three public collections quasar-42 improves mean average precision
by 4.1 points over BM25 alone, but the gain is not uniform: it disappears on
the shortest queries.

## Methods

We index documents with a fixed tokenizer and retrieve the top 200 candidates
with BM25. A locally hosted 8B parameter model then scores each candidate with
a short prompt. No query or document ever leaves the machine. We tuned the
re-ranking depth on a held-out split and report results on a separate test
split.

## Results

quasar-42 reaches 0.412 MAP on the test split, against 0.371 for BM25. The
improvement is largest for queries with three or more content words. For
one-word queries the two systems are statistically indistinguishable, which we
attribute to limited context in the re-ranking prompt.

## Limitations

The evaluation is limited to English collections and to a single local model.
We did not test on a cloud model, and we make no claim that quasar-42 is
generally better than larger hosted systems. Latency grows linearly with the
re-ranking depth, so interactive use requires a small depth.

## Discussion

The pattern suggests that local re-ranking helps most when the first stage
already returns a plausible set of candidates. When the candidate set is poor,
re-ranking cannot recover it, and the extra compute is wasted.
