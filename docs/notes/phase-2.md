# Phase 2: First-pass review

## What I built

A Prefect flow `review` that reads document ids from the Kafka topic, scores each paragraph 0-4
against a criterion with claude-haiku-5-5, and stores score, rationale, quote, model echo and
prompt version in Postgres, with a job row per run and a spend guard in front.
Demo: `uv run elusion review --criterion criteria/cloud-concentration.md --sample 20000`.

What partial failure looked like (verbatim, trimmed):

- Projected cost: `projected cost $1.38 for 20000 docs (mean $0.00007/doc over 20 probed); limit $10.00`
- Run 1, killed at 120 s: `Flow run 'neon-swallow' - Finished in state Crashed('Execution was aborted by an interrupt signal.')`.
  Job row `failed`, 549 scored, $0.0353. The paid work was kept.
- Run 2 resumed from the committed offset: `batch 1: 50 to score, 0 skipped`. Later, `batch 31: 0 to score, 50 skipped`
  for 40 batches: the topic has 1,608,140 messages for 1,604,015 documents (4,125 ids were published twice
  in phase 1) and the idempotency query absorbed them at zero cost.
- Run 2 crashed near batch 287 on `Invalid JSON: EOF while parsing a string ... "quote": "The Custod`,
  a reply cut off at max_tokens (12,236 scored, $0.7955). Fix: the provider now reads `stop_reason`
  and max_tokens is 1024. Re-running with `--sample 20` printed `batch 1: 0 to score, 20 skipped`
  for the crash's uncommitted batch.
- Totals: jobs' cost $0.834, scores' cost $0.834, so nothing was paid for twice. 12,906 distinct
  documents scored of 20,000; the remaining ~7,100 are scored by re-running the same command (follow-up).
- Scores 0:11565 1:1153 2:76 3:31 4:10. The score-4 quotes are about supplier and vendor
  concentration (an equipment packager, a custody vendor, a sole biologics supplier), not cloud.
  That is a loose prompt, to address in phase 3.

## What the tool is actually for

- Prefect flow/task: the server records each run's state, timing, crash and logs per task; a plain
  loop gives you the same work but a dead process leaves no record of where or why it stopped.
- Kafka consumer group and committed offset: the group is the named bookmark; the offset is
  "everything before here is done". It moves only after the Postgres insert, so a crash re-pulls
  a batch (harmless) and never skips one.
- Structured outputs and the cached system prompt: the API constrains the reply to the JSON
  schema (score, rationale, quote), and the long criterion text is cached so each paragraph pays
  only for its own tokens. A truncated or refused reply still has to be checked via `stop_reason`.
- The (model, prompt_version) key: a score belongs to the pair that produced it, so a new prompt
  or model adds rows beside the old ones and re-running with the same pair spends nothing.

## What a production e-discovery platform does differently, and why

Scale: 1.6 M paragraphs on one laptop consumer is a toy next to tens of millions of documents;
they would need many workers per partition, rate-limit budgets shared across them, and batch APIs.
Isolation: one matter's documents never share a queue, cache or prompt with another, because
a leak between matters is a legal problem. Defensibility: every score carries the prompt version and
the model the API echoed back, and the job table is an audit trail of who ran what and what it cost.

## The question I would ask their team

When a document is refused or truncated mid-review, what is the defensible handling: skip and log,
a human review queue, or re-ask with another model? And is the consumer group per prompt version
scoped per matter or global?
