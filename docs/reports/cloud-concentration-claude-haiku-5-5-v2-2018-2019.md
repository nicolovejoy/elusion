# Review report: cloud-concentration

- criterion: cloud-concentration
- review model: claude-haiku-5-5
- review prompt version: cloud-concentration/v2
- judge model: claude-opus-5-5
- judge prompt version: cloud-concentration/judge-v1
- judge effort: low
- date: 2026-11-07
- seed: 20261104

## Population

Every paragraph scored under the review model and prompt version above.

- population N: 17936
- flagged (score >= 3): 75
- discard (score < 3): 17861
- threshold: 3
- discard sample: 200 drawn with seed 20261104 from the pair's discard pile; 200 labeled by the judge

## Metrics

Point estimate and Clopper-Pearson 95% interval; ground truth is the judge's label.

- precision: 0.333 [0.229, 0.452] (k/n = 25/75)
- elusion: 0.000 [0.000, 0.018] (k/n = 0/200)
- richness: 0.001 [0.001, 0.020], derived from the two pile intervals
- recall: 1.000 [0.050, 1.000], derived from the two pile intervals

- precision: fraction of flagged paragraphs that are truly relevant.
- elusion: relevant rate inside the discard pile, measured by sampling it.
- richness: fraction of the whole population that is relevant.
- recall: fraction of truly relevant paragraphs the system flagged.

Richness and recall are not measured on a sample of their own: they combine the
precision and elusion intervals weighted by pile sizes. Both are monotone in
precision and elusion, and the two samples are independent, so each derived
interval covers the true value with probability at least about 90% (0.95 × 0.95),
not 95%.

## Judge agreement with the owner

Fraction of the owner's blind labels the judge matched.

- agreement: 0.800 [0.563, 0.943] (k/n = 16/20)

## Rationale quality

The grader (claude-opus-5-5) read each flagged paragraph with the reviewer's
rationale and quote and said whether the rationale is a faithful reading. The
quote check is mechanical: does the quote occur in the paragraph.

- grader model: claude-opus-5-5
- rationale sound: 0.787 [0.677, 0.873] (k/n = 59/75)
- quote verbatim: 0.987 [0.928, 1.000] (k/n = 74/75)

Rationale soundness against the judge's label, over the flagged paragraphs:

- sound and relevant: 23
- sound and not relevant: 36
- unsound and relevant: 2
- unsound and not relevant: 14

## Cost

- review run: $1.3422 (sum over 1 review_jobs rows)
- judge run: $2.4135 (whole criterion, every review pair: judge-runs ledger)
- grade run: $0.3134 (this review pair: grade-runs ledger)
- total for this pair (review + grade): $1.6556

## Wall-clock

- review run, summed over 1 review_jobs rows (each from its start to its finish): 0:55:46

## Companies found

Companies of the flagged paragraphs the judge labeled relevant, with paragraph counts.

- (no company name): 25
