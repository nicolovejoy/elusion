# ask-eval: auditable-decisions, claude-sonnet-5-5, ask/v1

- date: 2026-11-27
- index: 10034 abstracts, screened in by gpt-6-luna under auditable-decisions/v1 at threshold 3
- k: 5 abstracts read per question
- embedding model: text-embedding-3-small
- answer model: claude-sonnet-5-5, prompt version ask/v1
- judge model: claude-opus-5-5, prompt version ask/judge-v1
- reciprocal rank fusion: constant 60, each arm read 50 deep before fusing
- retrieval versions: lexical r2, vector r1, hybrid r2

## lexical

- recall@5: 16 of 25, 0.640 (95% interval 0.425 to 0.820)
- groundedness by claim: 30 of 53, 0.566 (95% interval 0.423 to 0.702)
- groundedness by question (every claim supported): 5 of 18, 0.278 (95% interval 0.097 to 0.535)
- claims in all answers: 53; answered questions with a claim: 18
- correct refusals (the model refused an unanswerable question): 5 of 5, 1.000 (95% interval 0.478 to 1.000); no passage retrieved, so no model was asked: 0
- wrong refusals (the model refused an answerable question): 7 of 25; no passage retrieved, so no model was asked: 0
- errors (no usable answer from the model): 0
- not judged (the judge gave no usable answer): 0 claims, 0 questions; they are in no groundedness figure
- cost: $0.8095

## vector

- recall@5: 12 of 25, 0.480 (95% interval 0.278 to 0.687)
- groundedness by claim: 24 of 50, 0.480 (95% interval 0.337 to 0.626)
- groundedness by question (every claim supported): 2 of 17, 0.118 (95% interval 0.015 to 0.364)
- claims in all answers: 50; answered questions with a claim: 17
- correct refusals (the model refused an unanswerable question): 5 of 5, 1.000 (95% interval 0.478 to 1.000); no passage retrieved, so no model was asked: 0
- wrong refusals (the model refused an answerable question): 8 of 25; no passage retrieved, so no model was asked: 0
- errors (no usable answer from the model): 0
- not judged (the judge gave no usable answer): 0 claims, 0 questions; they are in no groundedness figure
- cost: $0.4507

## hybrid

- recall@5: 17 of 25, 0.680 (95% interval 0.465 to 0.851)
- groundedness by claim: 29 of 65, 0.446 (95% interval 0.323 to 0.575)
- groundedness by question (every claim supported): 2 of 21, 0.095 (95% interval 0.012 to 0.304)
- claims in all answers: 65; answered questions with a claim: 21
- correct refusals (the model refused an unanswerable question): 5 of 5, 1.000 (95% interval 0.478 to 1.000); no passage retrieved, so no model was asked: 0
- wrong refusals (the model refused an answerable question): 4 of 25; no passage retrieved, so no model was asked: 0
- errors (no usable answer from the model): 0
- not judged (the judge gave no usable answer): 0 claims, 0 questions; they are in no groundedness figure
- cost: $0.5911

## Arms compared question by question

- lexical and vector, 25 answerable questions asked of both: lexical hit and vector missed 7; vector hit and lexical missed 3; both hit 9; both missed 6
- lexical and hybrid, 25 answerable questions asked of both: lexical hit and hybrid missed 2; hybrid hit and lexical missed 3; both hit 14; both missed 6
- vector and hybrid, 25 answerable questions asked of both: vector hit and hybrid missed 2; hybrid hit and vector missed 7; both hit 10; both missed 6

## Per question

- q01: lexical hit, answered; vector hit, answered; hybrid hit, failed
- q02: lexical hit, answered; vector hit, failed; hybrid hit, answered
- q04: lexical miss, answered; vector hit, answered; hybrid hit, answered
- q06: lexical hit, answered; vector hit, answered; hybrid hit, answered
- q07: lexical hit, answered; vector hit, answered; hybrid hit, answered
- q08: lexical miss, refused; vector hit, answered; hybrid miss, failed
- q09: lexical hit, answered; vector miss, refused; hybrid hit, failed
- q11: lexical miss, answered; vector miss, answered; hybrid miss, failed
- q12: lexical miss, refused; vector miss, refused; hybrid miss, refused
- q13: lexical hit, answered; vector miss, refused; hybrid hit, answered
- q16: lexical hit, failed; vector miss, answered; hybrid miss, answered
- q17: lexical hit, answered; vector hit, answered; hybrid hit, failed
- q20: lexical hit, failed; vector miss, refused; hybrid hit, answered
- q21: lexical hit, answered; vector hit, failed; hybrid hit, failed
- q24: lexical hit, answered; vector hit, answered; hybrid hit, answered
- q25: lexical hit, answered; vector miss, answered; hybrid hit, answered
- q26: lexical miss, refused; vector miss, answered; hybrid hit, answered
- q27: lexical hit, answered; vector hit, answered; hybrid hit, answered
- q29: lexical miss, refused; vector miss, refused; hybrid miss, refused
- q30: lexical miss, refused; vector miss, refused; hybrid miss, refused
- q31: lexical hit, answered; vector hit, answered; hybrid hit, answered
- q34: lexical hit, answered; vector miss, refused; hybrid hit, answered
- q35: lexical miss, refused; vector hit, answered; hybrid miss, refused
- q37: lexical hit, answered; vector miss, answered; hybrid miss, failed
- q38: lexical miss, refused; vector miss, refused; hybrid hit, failed
- u1: lexical refused; vector refused; hybrid refused
- u2: lexical refused; vector refused; hybrid refused
- u3: lexical refused; vector refused; hybrid refused
- u4: lexical refused; vector refused; hybrid refused
- u5: lexical refused; vector refused; hybrid refused

## Notes

- A search that finds no passage is refused without asking the model, because there is nothing to read. Nobody decided that refusal, so it is not counted as a correct or a wrong refusal; it is shown beside them.
- A result is kept per retrieval version of its arm. Results asked with an older version of an arm's search stay in the database and are in no figure here.
- An ask that was paid for but not recorded as a result (a failure in the one insert after it) is asked again on the next run, at a few cents.

## Read this with

- recall@5 is a lower bound: another abstract may also answer the question.
- The questions were drafted from abstracts, so they share vocabulary with their source even after rewording; this flatters lexical search.
- The draft prompt asked for other words than the abstract's, which works against lexical search, while the topic words the question and its source still share work for it; the net direction of the vocabulary bias is not known.
- With only 25 answerable questions the intervals are wide: a gap of a few questions between two arms may be chance. The three arms answer the same questions, so their intervals are correlated and overlap or no overlap is not the test; compare two arms on the questions where one hit the source and the other missed it, counted under Arms compared question by question.
- Groundedness by claim treats the claims of one answer as independent, which they are not; the by-question figure is the one to trust.
- The answer model and the judge are different models, so no model grades itself.
- Only abstracts the screen flagged are searched; one the screen discarded cannot be found.
