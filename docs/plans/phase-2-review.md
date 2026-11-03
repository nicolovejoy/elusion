# Phase 2 spec: first-pass review

Status: planned 2026-11-03, not started. Read `docs/ARCHITECTURE.md` (Phase 2 diagram)
and the Phase 2 checklist in `docs/PHASES.md` first. Hard rules in `CLAUDE.md` apply to
every line below.

## Goal

`uv run elusion review --criterion criteria/cloud-concentration.md --sample 20000`
scores 20,000 paragraphs with Claude Haiku 5.5, stores each score in Postgres, can be
killed and restarted without re-spending a token, and refuses to start if the projected
cost exceeds `ELUSION_MAX_RUN_USD`.

## Inputs already in place

- 1,604,015 documents in Postgres `documents` and `data/corpus/year=*.parquet` (text).
- 1.6 M ids on Kafka topic `documents.ingested`, keyed by `doc_id`.
- `ANTHROPIC_API_KEY` via `.env.tpl` + 1Password; `ELUSION_REVIEW_MODEL=claude-haiku-5-5`.

## Tasks, in order

Each task is test-first, one commit, `uv run ruff check . && uv run pytest` green.

### 1. Criterion and prompt files

- `criteria/cloud-concentration.md`: the criterion text from PROJECT.md, a few sentences.
- `src/elusion/review/prompts/cloud-concentration/v1.md`: the system prompt. States the
  criterion, the 0-4 scale with one line per level, and that the quote must be verbatim
  from the paragraph. Prompt version string = directory + file stem, e.g.
  `cloud-concentration/v1`.
- Loader: `review/prompts.py` -> `load_prompt(criterion, version) -> Prompt(text, version)`.
- Test: loads v1, version string is exact, missing version raises.

### 2. Provider interface (`review/providers.py`)

```python
class Verdict(BaseModel):            # pydantic, used as the structured-output schema
    score: int                       # 0-4, ge=0 le=4
    rationale: str
    quote: str

class Provider(Protocol):
    name: str                        # "anthropic"
    model: str                       # the pinned id from settings
    def review(self, system: str, paragraph: str) -> Review: ...

@dataclass
class Review:
    verdict: Verdict
    model: str                       # from the API response, not the request
    input_tokens: int
    output_tokens: int
    cost_usd: float
```

- Anthropic implementation uses `client.messages.parse()` with `Verdict` as the format
  (structured outputs; no prefill, no `tool_choice` forcing). `max_tokens` ~ 512,
  `output_config={"effort": "low"}`. The system prompt gets `cache_control` so 20k calls
  share one cached prefix.
- Prices live in one table in `review/pricing.py` keyed by model id; cost = tokens x price.
- Test: a fake provider returning a canned Verdict; the Anthropic class is tested with the
  SDK's client pointed at a stub `base_url` or skipped without a key. No live calls in
  the unit suite.

### 3. Tables (`review/db.py`)

```
scores(doc_id, model, prompt_version) PK
       score smallint, rationale text, quote text,
       input_tokens int, output_tokens int, cost_usd numeric(10,6),
       provider text, scored_at timestamptz
review_jobs(job_id uuid PK, criterion, model, prompt_version, sample_size,
       started_at, finished_at, status, docs_scored int, cost_usd numeric)
```

- `unscored_ids(engine, ids, model, prompt_version) -> list[str]`: the idempotency
  query. One `SELECT ... WHERE doc_id = ANY(:ids) AND NOT EXISTS (...)`.
- `insert_scores(...)` with `ON CONFLICT DO NOTHING`, `RETURNING` for the count.
- Test (needs Postgres): insert twice, second insert returns 0; `unscored_ids` shrinks.

### 4. Review flow (`flows/review.py`)

```
@flow review(criterion, sample, batch_size=50, concurrency=8)
  job = start_job(...)
  ids = sample_ids(sample)                 # Kafka consumer, group id = criterion/model/version
  for batch in chunks(ids, batch_size):
      todo = unscored_ids(batch)           # skip paid-for work
      texts = read_texts(todo)             # Parquet by doc_id, one open per year
      reviews = score_batch(texts)         # ThreadPool, concurrency limit, SDK retries
      insert_scores(reviews); update job counters; commit Kafka offsets
  finish_job(...)
```

- Kafka consumer group name encodes (criterion, model, prompt_version), so a new prompt
  version starts from the beginning of the topic and an old one resumes where it stopped.
  Offsets are committed only after the batch's scores are in Postgres.
- 429s: the SDK already retries with backoff (`max_retries=5` on the client). Count them.
- Parquet reads: load `doc_id -> text` for each year once per flow run into a dict
  (3 x 120 MB files; ~1 GB in memory is acceptable locally, note it).

### 5. Cost guard

- Before the loop: score 20 documents, take mean tokens per doc, project
  `sample x mean x price`. Refuse with a clear message if above `ELUSION_MAX_RUN_USD`.
  Log the projection either way. Those 20 scores are kept, not thrown away.

### 6. The proof (PHASES.md box 7)

- Run `--sample 20000`. After ~2 minutes, kill it (Ctrl-C). Note `docs_scored` and
  `cost_usd` on the job row. Restart with the same arguments. Show: new job row, first
  batches report "0 to score", total cost across both jobs ~ cost of one full run.
- Capture both terminals' tails into `docs/notes/phase-2.md`.

### 7. Phase note and ticks

`docs/notes/phase-2.md` with the four headings; tick all Phase 2 boxes; commit.

## Out of scope (ask first)

Second provider, Ollama, MLflow logging (phase 4); gold set (phase 3); any new dependency.

## Expected cost

20,000 paragraphs x ~350 input tokens x $0.10/M + ~80 output tokens x $0.50/M ~= $1.50
before caching; the cached system prompt lowers the input side. The $10 guard is not at risk.
