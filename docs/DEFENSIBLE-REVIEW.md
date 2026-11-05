# elusion, for a platform engineer at an e-discovery company

## What I set out to learn

I know systems and LLM applications. I did not know the ML-platform layer (orchestration,
experiment tracking, a model registry, distributed batch) or how a review proves what it
found. elusion uses each tool for its actual job on one realistic matter: find every 10-K
risk-factor paragraph that discloses a material dependence on one or a few cloud, hosting or
data-center providers. It covers 1.6 M paragraphs, of which 17,936 have been reviewed so
far.

## What the pipeline guarantees, and why each matters for defensible review

- **A versioned criterion and prompt.** Every score records the prompt version that produced
  it, so a result can be traced to the exact protocol behind it.
- **A pinned model snapshot.** An unpinned alias can change under you between the review and
  the validation, so the validation would describe a different reviewer.
- **Idempotent by document id.** A re-run never pays again for a document already scored
  under the same (model, prompt version). Re-running is safe, so nobody hesitates to do it.
- **Resumable.** A Kafka consumer group's offset moves only after the score is committed, so
  a crash costs at most one batch. This was tested by killing a run and resuming it.
- **Cost-bounded.** A run projected to cost more than a set limit is refused before the
  first call.
- **Validated by labeling, not asserted.** Precision comes from labeling the whole flagged
  pile; elusion from a seeded random sample of the discard pile; recall and richness are
  derived from the two. All of them carry Clopper-Pearson intervals and the sample sizes
  behind them. The gold set never enters a prompt.

## Where it stands

- precision 0.333 [0.229, 0.452], 25 of 75
- elusion 0.000 [0.000, 0.018], 0 of 200
- **recall is not established: [0.05, 1.00].** At a richness near 0.1%, a zero-hit sample of
  200 discards still allows about 320 misses against 25 found. Bounding the misses below
  25 needs about 2,500 discard labels (about $6.50 of judge time). That sample has not
  been run. The report's recall point estimate of 1.000 is an artefact of zero hits; only
  the interval carries information.
- An LLM (claude-opus-5-5) labeled the gold set; my 20 blind labels audit it: 16 of 20
  agree. All four disagreements traced to a paraphrase of the criterion that I labeled
  from. A paraphrased protocol is a different protocol. The discard half of the audit was
  ten easy no's, so it cannot catch a judge that misses relevant discards, and the 0 of 200
  behind elusion rests on the judge alone.

## What would differ at scale

- The elusion sample would be sized up front to the recall claim and the expected richness,
  not fixed at 200.
- The validation method would be agreed with the other side in advance, and the protocol
  would be given verbatim to every reviewer, human or model.
- Each matter would get its own queue, cache, prompts and gold set; sharing them across
  matters is a legal risk, not just an efficiency question.
- There would be many workers with a shared rate-limit budget and batch APIs, not one
  laptop consumer.

## Open questions

- At this richness, how does a platform prove recall to opposing counsel, and who labels
  that sample?
- When a document is refused or truncated mid-review, which handling holds up: skip and
  log, a human queue, or re-asking with another model?
- How much can an LLM judge's labels count as ground truth, and how large must the human
  audit of it be?

Details: `docs/reports/`, `docs/notes/phase-2.md`, `docs/notes/phase-3.md`.
