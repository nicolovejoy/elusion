# Phase 2: First-pass review

## What I built

A Prefect flow `review` reads document ids from the Kafka topic, scores each paragraph 0-4 against a
criterion with claude-haiku-5-5, and stores score, rationale, quote, model echo and prompt version in
Postgres, with a job row per run and a spend guard in front. Demo: `uv run elusion review --criterion criteria/cloud-concentration.md --sample 20000`.

## What the tool is actually for

- Prefect flow/task: the server records each run's state, timing, crash and logs per task; a plain
  loop does the same work but a dead process leaves no record of where or why it stopped.
- Kafka consumer group and committed offset: the group is a named bookmark; the offset means
  "everything before here is done". It moves only after the Postgres insert. `--sample N` pulls the
  *next* N ids for the group, so a re-run continues the stream rather than completing a fixed sample.
  A defined sample population (a job_id or sample tag on scores) is open for phase 3.
- Structured outputs and the cached system prompt: the reply is schema-constrained and the criterion is
  cached; `stop_reason` still has to be checked.
- The (model, prompt_version) key: a score belongs to the pair that produced it, so re-running the same
  pair spends nothing on documents already scored.

What partial failure looked like (verbatim, trimmed):

- `projected cost $1.38 for 20000 docs (mean $0.00007/doc over 20 probed); limit $10.00`
- Run 1 killed at 120 s: `Finished in state Crashed('Execution was aborted by an interrupt signal.')`;
  job `failed`, 549 scored, $0.0353.
- Run 2 resumed: `batch 1: 50 to score, 0 skipped`, later `batch 31: 0 to score, 50 skipped` (4,125 ids were published twice). It crashed on a reply cut off at max_tokens; fixed by
  reading `stop_reason`, max_tokens 1024. 12,906 distinct documents scored, $0.834.
- Jobs' cost equals scores' cost. That shows every *recorded* call was recorded once. A kill mid-batch
  loses the in-flight batch's billed-but-unsettled reviews (at most batch_size - 1 documents, about
  $0.004), which neither sum can see, and the resume pays for them again.
- HTTP retries (429/5xx) are delegated to the SDK (max_retries=5) and not counted. A refused or truncated
  document is not retried under the same consumer group: its batch offset is committed.

## What a production e-discovery platform does differently, and why

Scale: many workers per partition, shared rate-limit budgets and batch APIs, not one laptop consumer.
Isolation: one matter's documents never share a queue, cache or prompt with another's; a leak is a
legal problem. Defensibility: every score carries the prompt version and the echoed model, and the job
table is an audit trail of who ran what and what it cost.

## The question I would ask their team

When a document is refused or truncated mid-review, what is the defensible handling: skip and log, a
human review queue, or re-ask with another model? Is the consumer group per matter or global?
