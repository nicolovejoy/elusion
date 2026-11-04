# Phase 3 spec: validation

Status: designed 2026-11-03, not started. Read `docs/ARCHITECTURE.md` (Phase 3 lines) and
the Phase 3 checklist in `docs/PHASES.md` first. Hard rules in `CLAUDE.md` apply to every
line below. The phase 2 spec (`docs/plans/phase-2-review.md`) is the pattern this one
follows.

## Intent

Phase 2 scored paragraphs. Phase 3 says how good those scores are, with error bars, in the
vocabulary an e-discovery team uses: recall, precision, elusion, richness, each with a
Clopper-Pearson 95% interval and the sample size behind it. The point is understanding
defensible review, not features.

Two facts shape the design:

- Relevant paragraphs are rare. Under prompt v1, 11,565 of 12,906 scored 0 and only 117
  scored 2 or more. A uniform random gold sample of 300 would hold about three positives.
- A gold label says whether a paragraph meets the criterion. It belongs to the document,
  not to any model or prompt. A gold set outlives prompt versions.

## Decisions already made (by the owner, 2026-11-03)

1. **The criterion is tightened first.** "Cloud concentration" means dependence on cloud,
   hosting or data-center providers. Manufacturing suppliers, component or chip vendors,
   payment processors, and generic "third parties" do not count. The v1 scores answered
   the looser question (their top hits were single-supplier risk), so they are not reused.
2. **Prompt v2 and a full rescore under v2.** The 20,000-paragraph sample is scored again
   under `cloud-concentration/v2` with the pinned review model. v1 scores stay in the
   table as the phase 4 comparison. Nobody runs more v1 scoring.
3. **A judge model labels; the owner audits.** The owner can label about 20 paragraphs,
   not 300. So Claude Opus 5.5 (`ELUSION_JUDGE_MODEL`) labels the gold set, and the
   owner's 20 are a blind audit of the judge. The report shows both the metrics from the
   judge's labels and the judge's agreement with the owner, each with an interval. The
   phase note says plainly that an LLM judged an LLM and that 20 human labels are the
   only human ground truth. This pulls the judge model forward from phase 4; phase 4's
   judge item (grading rationales) is a different use and stays in phase 4.
4. **Two piles.** Score 3 and above is flagged (owner decision 2026-11-04: level 2 is by
   definition not a dependency risk); 0, 1 and 2 is "discard". The judge labels
   every flagged paragraph and a uniform random sample of 200 from the discard pile.
   Precision and elusion are then direct Clopper-Pearson proportions. Richness and recall
   are combined from the two piles using the pile sizes.
5. **Population** = every document that has a score for the (review model, prompt
   version) pair. This also settles the phase 2 open question: `--sample N` stays "the
   next N ids for the consumer group" and no column is added to `scores`.

## Goal

```
uv run elusion review   --criterion criteria/cloud-concentration.md --sample 20000 --prompt-version v2
uv run elusion judge    --criterion criteria/cloud-concentration.md --prompt-version v2
uv run elusion label    --criterion criteria/cloud-concentration.md --prompt-version v2 --n 20
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2
```

The last command writes one Markdown report page with the four metrics, intervals, sample
sizes, cost, wall-clock, judge agreement, and the companies found.

## Inputs already in place

- `scores` and `review_jobs` tables (`review/db.py`); `documents` table with `cik`,
  `company`, `year`; Parquet text via `review/corpus.py:load_texts`.
- `AnthropicProvider` with structured outputs, `pricing.py`, `load_prompt`.
- `settings.review_model`, `settings.max_run_usd`. `ELUSION_JUDGE_MODEL` is in
  `.env.tpl` and `.env.example` but not yet in `config.py`.
- `scipy` is already a dependency (`pyproject.toml`).

## Tasks, in order

Each code task is test-first, one commit, `uv run ruff check . && uv run pytest` green.
Tasks marked **owner** are run by the owner at the terminal because they spend money or
need `.env`; the controller hands over the exact command and waits.

### 1. Criterion v2, prompt v2, judge prompt, settings, pricing

- Rewrite `criteria/cloud-concentration.md` to decision 1: definition, two or three
  positive examples in prose (named provider dependence, single data-center dependence,
  "a small number of hosting providers"), and an explicit exclusion list (suppliers,
  component vendors, contract manufacturers, payment processors, generic third parties,
  the company's *own* data centers unless it says it depends on a third party to run
  them).
- `src/elusion/review/prompts/cloud-concentration/v2.md`: same shape as v1 (0-4 scale,
  verbatim quote, JSON fields unchanged so `Verdict` still fits), with the tightened
  criterion and a line per exclusion. Levels 2, 3 and 4 keep their v1 meaning.
  Score 3 and above is flagged (owner decision 2026-11-04: level 2 is by definition not a
  dependency risk). Level 1 becomes "mentions dependence on a vendor
  or supplier that is not a cloud, hosting or data-center provider", so supplier risk has
  a home below the flag line. v1.md is never edited.
- `src/elusion/review/prompts/cloud-concentration/judge-v1.md`: the judge's system
  prompt. States the tightened criterion in full, asks for a yes/no answer on whether
  the paragraph meets it, plus a one-sentence rationale. No scale. Version string
  `cloud-concentration/judge-v1` via `load_prompt(criterion, "judge-v1")`.
- `config.py`: `judge_model` from `ELUSION_JUDGE_MODEL`, default `claude-opus-5-5`.
- `pricing.py`: add the Opus 5.5 row. The builder looks the prices up (the `claude-api`
  skill or docs.anthropic.com), does not recall them, and adds a `DECISIONS.md` line with
  the source and date.
- Tests: v2 and judge-v1 load with exact version strings; settings default; pricing row
  present and `cost_usd` works for it.

### 2. Owner: rescore under v2

```
uv run elusion review --criterion criteria/cloud-concentration.md --sample 20000 --prompt-version v2
```

A new consumer group starts from the beginning of the topic, so this covers roughly the
same first 20,000 ids as the v1 run. Expected cost about $1.30, about 25 minutes. Record
the job row's `docs_scored`, `cost_usd` and the score distribution in the phase note.

### 3. Gold set module and judge flow

- `src/elusion/evaluate/gold.py`:
  - One CSV per criterion at `data/gold/<criterion>.csv` (gitignored under `data/`).
    Columns: `doc_id, label (0/1), pile (flagged|discard), labeler, prompt_version,
    rationale, labeled_at`. `labeler` is the judge's pinned model id for judge rows and
    the literal `human` for owner rows. `prompt_version` is the judge prompt version for
    judge rows and empty for human rows. Rows are appended; a (doc_id, labeler) pair
    appears at most once.
  - `select_gold(engine, model, prompt_version, threshold=2, discard_n=200, seed=...)
    -> list[(doc_id, pile)]`: every doc with score >= threshold, plus `discard_n` drawn
    uniformly without replacement from docs with score < threshold, using a seeded RNG.
    The seed is a CLI option with a fixed default and is written into the report.
  - Judge failures (refusal, truncation, invalid JSON) write no row, so a re-run retries
    exactly those ids.
  - Read helpers that return the judge rows and the human rows separately.
- Judge call: goes through `review/providers.py`. The builder decides whether to
  generalise `AnthropicProvider` over the output schema or add a `label()` method; either
  way the call carries the pinned judge model, the judge prompt version, structured
  output, cached system prompt, the model-echo check from phase 2, and `effort: low`
  like the review call (recorded in the report). The judge never
  sees any score, rationale, quote or existing label: paragraph text only.
- `flows/evaluate.py`, `@flow(name="judge")`: select the gold ids, skip ids the judge
  already labeled (idempotent on (doc_id, labeler)), probe 20 and project cost against
  `settings.max_run_usd` exactly as the review flow does, label with bounded
  concurrency, append rows to the CSV after each batch. Returns a summary (labeled,
  skipped, failed, cost).
- CLI `elusion judge --criterion --prompt-version [--threshold 2] [--discard-n 200]
  [--seed] [--max-run-usd]`.
- Tests: `select_gold` on a seeded in-memory score set (counts, no overlap, determinism);
  CSV append and dedupe; the flow with a fake judge and a fake id set; no live calls.

### 4. Owner: run the judge

```
uv run elusion judge --criterion criteria/cloud-concentration.md --prompt-version v2
```

About 300 to 400 paragraphs on Opus 5.5. The spend guard applies. Record the cost.

### 5. Labeling CLI

- `elusion label --criterion --prompt-version --n 20`: from the judge-labeled rows, pick
  `n/2` flagged and `n/2` discard uniformly at random with a seed, excluding any doc the
  owner already labeled. (Owner's choice 2026-11-04: by pile, not by judge answer. The
  discard half will mostly be trivial agreement; the note says the audit measures overall
  agreement and cannot detect judge misses in the discard pile with n=20.) For each: print the paragraph text and nothing else (no score,
  no judge answer, no company name), read one key: `y`, `n`, `s` (skip) or `q` (quit,
  keeping what was answered). Append a `human` row per answer. Skips are not written.
- A paragraph is shown as plain wrapped text in the terminal; no TUI library, no new
  dependency.
- Tests: selection excludes already-labeled docs and balances piles; input handling via
  an injected reader; rows written correctly.

### 6. Owner: label 20

```
uv run elusion label --criterion criteria/cloud-concentration.md --prompt-version v2 --n 20
```

### 7. Metrics

- `src/elusion/evaluate/metrics.py`:
  - `clopper_pearson(k, n, confidence=0.95) -> (low, high)` using `scipy.stats.beta`.
    Handles k=0 and k=n.
  - `Estimate(point, low, high, k, n)` dataclass.
  - `precision(flagged_rows)`, `elusion(discard_rows)`: direct proportions.
  - `richness(precision, elusion, n_flagged, n_discard)` and
    `recall(precision, elusion, n_flagged, n_discard)`: point estimates from the two
    piles weighted by pile sizes. Their intervals are formed by evaluating the formula
    at the Clopper-Pearson bounds of its inputs (worst and best case), and the report
    labels them "derived from the two pile intervals" so a reader does not take them
    for exact 95% intervals. The phase note explains why elusion is measured and recall
    is derived, and what a skeptic would ask.
  - `agreement(human_rows, judge_rows)`: fraction of the owner's labels the judge
    matched, as an `Estimate`.
- Tests with hand-computed values, including the k=0 edge, and a check that recall
  equals 1 when elusion is 0.

### 8. Report and evaluate flow

- `src/elusion/evaluate/report.py`: renders one Markdown page from a `RunReport`
  dataclass. Sections: header (criterion, review model, prompt version, judge model,
  judge prompt version, date, seed), population (N, N flagged, N discard, threshold),
  the four metrics each as `point [low, high] (k/n)`, judge agreement with the owner,
  cost (review run and judge run, from `review_jobs` and the judge summary),
  wall-clock of the review run, and companies found (distinct `documents.company` for
  flagged docs the judge labeled 1, with counts, from Postgres).
- `flows/evaluate.py`, `@flow(name="evaluate")`: loads scores for the pair and the gold
  rows, then **recomputes the piles from `scores`** rather than trusting the CSV `pile`
  column (which is informational). Flagged = every doc with score >= threshold under the
  pair; it must have a judge label for every member, otherwise evaluate refuses and says
  how many are missing (re-running `judge` fills them, since failed ids were never
  written). Discard sample = judge-labeled rows whose doc currently scores below the
  threshold under the pair; its n is whatever that intersection is, printed in the
  report. Cost is summed over every `review_jobs` row for the pair; wall-clock is first
  start to last finish across those rows, labeled as such. Computes metrics, writes `docs/reports/<criterion>-<model>-<prompt_version>.md`
  (committed; it is the artifact the project exists to produce) and prints the path.
  Phase 4 will log the same file to MLflow.
- CLI `elusion evaluate --criterion --prompt-version`. The `CLAUDE.md` command line
  `elusion evaluate --run <mlflow run id>` is the phase 4 form; update the comment there.
- Tests: report renders from a fixed `RunReport` and contains every section; the flow
  with a fake engine and a temp CSV.

### 9. Owner: evaluate, then phase note and ticks

```
uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2
```

`docs/notes/phase-3.md` with the four headings, including the metric definitions in the
owner's words and the skeptic's questions; tick all Phase 3 boxes; `DECISIONS.md` lines
for: criterion tightened and why; judge model in phase 3; two-pile design; population
definition; derived intervals for recall and richness; report location. Update
`docs/ARCHITECTURE.md` phase 3 lines to show the judge.

## Non-goals and anti-goals

- No MLflow, no second provider, no Ollama (phase 4).
- No stratification beyond two piles, no Horvitz-Thompson weighting.
- No new dependency; scipy is already present.
- Anti-goal: any path by which gold labels, judge labels or the owner's labels reach a
  review or judge prompt. The judge sees paragraph text only.
- Richness and recall describe the scored population (the first 20,000 topic ids, which
  are in ingest order, not a random draw from the corpus). The report says "population",
  never "corpus".
- Anti-goal: presenting the judge's labels as human ground truth. The report and note
  always say who labeled what.

## Left to the builder

- How the judge call is shaped inside `providers.py` (generalised schema vs a second
  method), as long as the hard rules hold.
- CSV handling (stdlib `csv` is fine).
- Exact Markdown layout of the report.
- The fixed default seed value.

## Expected cost

Review v2: about $1.30. Judge: 300 to 400 Opus 5.5 calls on short paragraphs, estimated
under $3 once the price row is in; the spend guard checks it. Total under $5.
