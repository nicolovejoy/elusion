# Concept board: build plan for the first pass (stopping rules)

Status: approved by the owner in chat on 2026-11-26, built task by task from this page.

Spec: `docs/plans/phase-7-concept-audit.md` (its "Decided by the owner, 2026-11-26" block
governs older lines) and `docs/plans/phase-7-concept-board-map.md`.

What is built: an append-only log table (`concept_log`), a first read of each matching
abstract by a pinned model, and a local web page where the owner audits those readings
blind, settles the disagreements, and then sees the concept card and paper notes.

## Global Constraints

- Python 3.12, `uv`, `ruff` (run `uv run ruff check` and `uv run ruff format`), `pytest`.
  Follow the patterns of the existing modules: SQLAlchemy Core with `psycopg`, a module
  docstring that explains the idea in plain words, tests that skip when Postgres is not
  reachable (`tests/conftest.py::requires_service`).
- The only new declared dependency is `flask` (already installed through MLflow). Nothing
  else is added to `pyproject.toml`. No JavaScript, no build step.
- `concept_log` takes only inserts. The database itself refuses UPDATE, DELETE and
  TRUNCATE; code never tries them.
- Every model call carries a pinned model id and a prompt version string, and both are
  stored on the row. A (concept, version, doc_id, model, prompt_version) that already has
  a reading row is never sent to a model again, whatever the first outcome was.
- Nothing from `data/gold/`, and no audit verdict or decision, ever enters a prompt.
- Blindness: no page the owner can reach before the blind step is finished shows any
  reading, verdict, quote or rationale of the reader's.
- Timestamps are stored in UTC and shown in `America/Los_Angeles`.
- Tests never touch the compose database's `concept_log`: they use the `board_engine`
  fixture of Task 1 (a throwaway database), and make no network call.
- Verdict words, exact: `holds`, `does_not_hold`, `skip`, `failed`. Entry kinds, exact:
  `concept`, `reading`, `audit`, `decision`.
- Commit at the end of each task, on the current branch `phase-7-concept-board`. Commit
  message ends with the line `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.
  Do not push.

## Task 1: the log

Create `src/elusion/board/__init__.py` (empty), `src/elusion/board/quotes.py`,
`src/elusion/board/log.py`, `tests/test_board_quotes.py`, `tests/test_board_log.py`, and
add the `board_engine` fixture to `tests/conftest.py`. Tests first.

**`quotes.py`**

```python
def find_quote(text: str, quote: str) -> tuple[int, int] | None:
```

Returns the (start, end) span in `text` of the first place `quote` appears, or None.
Matching is exact character for character (case-sensitive) except that any run of
whitespace in the quote matches any run of whitespace in the text; leading and trailing
whitespace of the quote is ignored. A quote that is empty or all whitespace returns None.
Build a regex from `re.escape` of the quote's whitespace-separated tokens joined by `\s+`.

**`log.py`**: one table, SQLAlchemy Core, named `concept_log`:

- `entry_id` BigInteger identity primary key
- `kind` Text not null, CHECK in the four kinds
- `concept` Text not null (a slug such as `stopping`)
- `concept_version` Integer not null
- `doc_id` String(16) null
- `verdict` Text null, CHECK null or one of the four verdict words
- `quote` Text null
- `body` Text null (concept: the wording; reading: the rationale; decision: the reason)
- `actor` Text not null (`owner`, `claude`, `owner+claude`, or `model`)
- `model` Text null, `prompt_version` Text null
- `detail` JSONB not null, server default `{}`
- `written_at` DateTime(timezone=True) not null, server default `now()`

Indexes: unique on (`concept`, `concept_version`) where `kind = 'concept'`; unique on
(`concept`, `concept_version`, `doc_id`, `model`, `prompt_version`) where
`kind = 'reading'`.

`ensure_schema(engine)` creates the table and indexes, then (idempotently, with
`CREATE OR REPLACE FUNCTION` and `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER`) a function
`concept_log_refuse_change()` that raises the exception `concept_log is append-only`, a
row-level trigger `BEFORE UPDATE OR DELETE` and a statement-level trigger
`BEFORE TRUNCATE`, both calling it.

Exceptions: `QuoteNotVerbatim(ValueError)`, `AlreadyAudited(ValueError)`,
`UnknownConcept(LookupError)`.

Functions (all take `engine` first; all writes are single inserts returning `entry_id`):

- `add_concept(engine, concept, wording, actor, detail=None) -> int`: returns the new
  version, 1 for a new concept, else the highest existing version plus 1.
- `current_concept(engine, concept) -> Entry`: the concept entry with the highest version;
  raises `UnknownConcept`.
- `add_reading(engine, *, concept, version, doc_id, verdict, quote, rationale, model,
  prompt_version, text, detail=None) -> int | None`: `verdict` is `holds`,
  `does_not_hold` or `failed`. `text` is the stored abstract. For `holds` the quote must
  be found by `find_quote`, else `QuoteNotVerbatim`. For `does_not_hold` a non-empty quote
  must be found too, and an empty one is stored as null. For `failed` the quote is stored
  as null whatever was passed. Actor is `model`. Returns None, inserting nothing, when the
  unique reading key already exists (`ON CONFLICT DO NOTHING`).
- `read_doc_ids(engine, concept, version, model, prompt_version) -> set[str]`.
- `add_audit(engine, concept, version, doc_id, verdict) -> int`: `holds`,
  `does_not_hold` or `skip`, actor `owner`. Raises `AlreadyAudited` when the paper already
  has a `holds` or `does_not_hold` audit entry for that concept version (a blind verdict
  is given once; any number of skips may precede it).
- `add_decision(engine, concept, version, *, reason, actor, doc_id=None, verdict=None,
  rests_on=()) -> int`: `reason` must be non-empty after stripping, else `ValueError`;
  `rests_on` (entry ids) is stored in `detail["rests_on"]`.
- `entries(engine, concept, version=None, kind=None) -> list[Entry]`: in `entry_id` order.

`Entry` is a frozen dataclass with one field per column.

**`board_engine` fixture** (session scope, in `tests/conftest.py`): connects to the
compose Postgres with AUTOCOMMIT, runs `CREATE DATABASE elusion_test_<8 hex chars>`,
yields an engine on that database with `log.ensure_schema` applied, then disposes it and
runs `DROP DATABASE ... WITH (FORCE)`. It calls `pytest.skip` when Postgres on
localhost:5433 is not reachable. Each test uses its own concept slug (a uuid fragment), so
tests do not see each other's rows.

**Tests** (each one behaviour): `find_quote` exact, across a line break, case differs
(None), empty (None), span is correct for slicing; an UPDATE is refused by the database; a
DELETE is refused; a TRUNCATE is refused; `ensure_schema` can run twice; versions go 1, 2;
`current_concept` unknown raises; a `holds` reading with a quote not in the text raises
and inserts nothing; a `holds` reading with an empty quote raises; a second reading under
the same key returns None and leaves one row; a `failed` reading stores a null quote;
`add_audit` allows skip then holds, and refuses a second verdict; `add_decision` refuses
an empty reason and stores `rests_on`; `written_at` is timezone-aware.

## Task 2: the first read

Create `src/elusion/board/batch.py`, `src/elusion/review/prompts/first-read/v1.md`,
`src/elusion/flows/first_read.py`, tests `tests/test_board_batch.py` and
`tests/test_first_read_flow.py`; extend `src/elusion/review/providers.py`,
`tests/fakes.py` and `src/elusion/cli.py` (with tests in `tests/test_cli.py` in the style
already there). Tests first.

**`batch.py`**

```python
@dataclass(frozen=True)
class Paper:
    doc_id: str
    work_id: str
    title: str
    publication_date: str
    text: str

def load_papers(abstracts_dir: Path) -> list[Paper]
def word_search(papers: Iterable[Paper], terms: Sequence[str]) -> list[Paper]
def held_back_ids(gold_csv: Path) -> set[str]
```

`load_papers` reads every `year=*.parquet` under the directory (columns as in
`collect/abstracts.py`). `word_search` keeps a paper when its text contains any term as a
case-insensitive substring, and returns them sorted by `doc_id`; an empty `terms` raises
`ValueError`. `held_back_ids` is every `doc_id` in the gold CSV (via
`evaluate.gold.read_rows`), or the empty set when the file does not exist.

**Prompt file `first-read/v1.md`**, exactly this text:

```
You read one paper abstract against one concept and say whether the paper holds it.

A concept is stated as a question that papers in a field try to answer. A paper holds the
concept when it proposes, uses, compares, evaluates or argues about an answer to that
question, in the sense the concept states. A paper that uses the same words for a
different thing does not hold it.

You are given the concept after CONCEPT: and the abstract after ABSTRACT:. Use only the
abstract. Answer with these fields:

- holds: true if the paper holds the concept, false if it does not.
- quote: if holds is true, the one passage of the abstract that best shows it. If holds is
  false and the abstract uses the concept's words in another sense, the passage where it
  does so. Otherwise an empty string. The quote must be copied character for character
  from the abstract: no ellipsis, no corrections, no words left out, nothing joined from
  two places. Keep it under 40 words.
- answer: if holds is true, one sentence stating the answer this paper gives to the
  concept's question, in your own words. Otherwise an empty string.
- rationale: one sentence saying why the paper holds the concept or does not.
```

**`providers.py`** gains, beside `Label` and `Grade`: a pydantic model `Reading`
(`holds: bool`, `quote: str`, `answer: str`, `rationale: str`, `extra="forbid"`, each
field with a description), `READING_SCHEMA`, a dataclass `FirstRead` (`reading: Reading`,
then the same fields as `Judgement`), a `Reader` Protocol (`name`, `model`,
`read(system, message) -> FirstRead`), `reader_message(wording: str, abstract: str) ->
str` returning `f"CONCEPT:\n{wording}\n\nABSTRACT:\n{abstract}"`, and
`AnthropicProvider.read`, which goes through the existing `_call`. `tests/fakes.py` gains
`FakeReader`: built with a function `(message) -> Reading | Exception`; it records
`calls` as (system, message) and raises the exception when given one.

**`flows/first_read.py`**: a Prefect flow in the style of the other flows in that
directory (read `flows/grade.py` and its test for how a flow is written and tested here).

```python
def first_read(concept: str, reader: Reader, *, engine=None, abstracts_dir=None,
               prompt_version: str = "v1", max_usd: float | None = None) -> dict
```

1. `current_concept`; its `detail["search_terms"]` is the term list (missing or empty:
   `ValueError`).
2. `word_search` over `load_papers`; drop papers in `read_doc_ids` for
   (version, `reader.model`, `"first-read/v1"`).
3. Spend guard before any call: projected cost is the sum over unread papers of
   `cost_usd(reader.model, len(text) // 3 + 600, 300)`. If it exceeds `max_usd`
   (default `settings.max_run_usd`) raise `SpendGuardError` (reuse the one the review
   flow already has if it exists; otherwise define it in this module) naming both figures.
4. For each unread paper call `reader.read(prompt.text, reader_message(wording, text))`.
   - A `ReviewError` is stored as a `failed` reading with
     `detail = {"stop_reason": ..., tokens, "cost_usd": ...}` (price its tokens with
     `cost_usd`).
   - Otherwise try `add_reading` with verdict `holds` or `does_not_hold`, the quote, the
     rationale, and `detail = {"answer", "served_model", "input_tokens", "output_tokens",
     "cost_usd"}`. On `QuoteNotVerbatim` store a `failed` reading instead, whose detail
     also carries `"stop_reason": "quote_not_verbatim"`, `"model_holds"` and
     `"model_quote"`.
   - `model` on the row is `reader.model` (the pinned id); the id the API served goes in
     `detail["served_model"]`.
5. Return `{"matched", "already_read", "read", "holds", "does_not_hold", "failed",
   "cost_usd"}`.

**CLI**

- `elusion concept --name stopping --wording "..." --term "..." [--term ...]
  [--criterion auditable-decisions] [--actor claude]`: `add_concept` with
  `detail = {"search_terms": [...], "criterion": ...}`; prints the new version.
- `elusion first-read --concept stopping [--model claude-haiku-5-5]`: builds
  `AnthropicProvider(model)`, runs the flow, prints the summary. `--model` defaults to
  the literal `claude-haiku-5-5`.

**Tests**: `word_search` is case-insensitive, sorted, refuses no terms; `held_back_ids`
with and without a file; the message the reader receives is exactly
`reader_message(wording, text)` and the system text is the prompt file (so nothing else
can reach the prompt); a holds reading is stored with model, prompt version
`first-read/v1`, quote and `detail["answer"]`; a quote that is not verbatim becomes a
`failed` row with the model's quote in detail; a `ReviewError` becomes a `failed` row; a
second run makes no call and reports `already_read`; a failed paper is not called again
on the second run; the spend guard raises before any call; the two CLI commands.

## Task 3: the audit page

Add `flask` to `dependencies` in `pyproject.toml` (run `uv lock`; it must download
nothing new). Create `src/elusion/board/audit.py` (pure logic over the log),
`src/elusion/board/web.py` (the Flask app), templates under
`src/elusion/board/templates/` (`base.html`, `audit.html`, `disagreement.html`,
`summary.html`, `wait.html`), tests `tests/test_board_audit.py` and
`tests/test_board_web.py`, and the `board` command in `cli.py`. Tests first.

**`audit.py`**: everything is computed from the log; nothing is cached.

```python
@dataclass(frozen=True)
class Reader:            # whose readings the audit compares against
    model: str
    prompt_version: str

def reader_of(engine, concept, version) -> Reader
def audit_order(doc_ids, concept, version) -> list[str]
def blind_verdicts(engine, concept, version) -> dict[str, Entry]
def next_blind(engine, concept, version, doc_ids) -> str | None
def disagreements(engine, concept, version, reader, doc_ids) -> list[Disagreement]
def final_verdicts(engine, concept, version, doc_ids) -> dict[str, str]
def summary(engine, concept, version, reader, doc_ids, held_back_n) -> Summary
```

- `reader_of`: the single (model, prompt_version) pair among the version's reading
  entries; `ValueError` when there are none or more than one pair.
- `audit_order`: the sorted ids shuffled by `random.Random(f"{concept}:{version}")`.
- `blind_verdicts`: per paper, its one `holds` / `does_not_hold` audit entry.
- `next_blind`: the first paper in `audit_order` with no audit entry at all; when every
  paper has one, the skipped paper without a verdict whose latest skip is oldest; None
  when every paper has a verdict.
- `Disagreement`: `doc_id`, the owner's blind `Entry`, the reader's reading `Entry`, and
  `decision: Entry | None`. A paper is a disagreement when its reading is `holds` or
  `does_not_hold` and differs from the blind verdict. A `failed` reading is never one.
- `final_verdicts`: the decision's verdict where a decision with that `doc_id` and a
  verdict exists (the latest one), else the blind verdict.
- `Summary` (frozen dataclass): `audited`, `compared` (papers with a blind verdict and a
  non-failed reading), `agreed`, `agreement: Estimate` (`evaluate.metrics.proportion(
  agreed, compared)`), `holds_final`, `precision: Estimate` (`proportion(holds_final,
  audited)`), `failed_readings`, `unsettled` (disagreements with no decision),
  `held_back`. `doc_ids` passed to these functions never include held-back papers.

**`web.py`**

```python
def create_app(engine, concept: str, papers: dict[str, Paper], held_back: set[str]) -> Flask
```

The app holds the concept slug, the papers that have a reading under the current version
(minus `held_back`), and the engine. Routes:

- `GET /` redirects to `/audit`.
- `GET /audit`: the concept's wording, "paper k of n", the paper's title and abstract,
  and three buttons `Holds`, `Does not hold`, `Skip`, each a POST form to
  `/audit/<doc_id>` with field `verdict`. When `next_blind` is None it redirects to
  `/audit/disagreements`. The response contains nothing from any reading entry.
- `POST /audit/<doc_id>`: `add_audit`, then redirect (303) to `/audit`. Unknown doc id:
  404. Bad verdict: 400. `AlreadyAudited`: 409.
- `GET /audit/disagreements`: while any paper has no blind verdict, status 409 with
  `wait.html` (how many remain, link to `/audit`). Otherwise the first disagreement with
  no decision: title, the abstract with the reader's quote wrapped in `<mark>` in place
  (span from `find_quote`; everything HTML-escaped), the owner's verdict, the reader's
  verdict, rationale, model and prompt version, and a form posting `verdict`
  (`holds` / `does_not_hold`) and `reason` to `/audit/disagreements/<doc_id>`. When none
  remain it redirects to `/audit/summary`.
- `POST /audit/disagreements/<doc_id>`: `add_decision` with actor `owner`, the verdict,
  the reason, `rests_on` = the audit and reading entry ids; redirect (303) back. An empty
  reason: 400, nothing stored. Refused with 409 while the blind step is unfinished.
- `GET /audit/summary`: the `Summary` figures in words: agreement and the precision of
  the word search, each as `k of n`, the proportion and its Clopper-Pearson 95% interval;
  failed readings; held-back papers; unsettled disagreements. Before the blind step is
  finished it shows only the progress count and no figure.
- Every POST whose `Origin` header is present and differs from the request's own
  scheme-and-host gets 403 (the page is on localhost, and another site must not be able
  to post verdicts).
- Templates: plain server-rendered HTML with one inline `<style>` in `base.html`
  (readable measure, system font, works in light and dark through
  `prefers-color-scheme`). A nav line links Audit, Disagreements, Summary.

**CLI**: `elusion board --concept stopping [--port 8010] [--release-gold]`. Loads the
current concept, the papers (`load_papers`, keyed by `doc_id`, kept when they have a
reading), `held_back_ids(gold_path(detail["criterion"], settings.data_dir))` unless
`--release-gold`, prints the URL `http://localhost:8010` and how many papers are held
back, and runs the app on `127.0.0.1` with `debug=False`.

**Tests** (Flask's test client against `board_engine`; seed the log through `log.py`):
`audit_order` is the same on every call and differs from sorted order for a large enough
list; `next_blind` serves never-seen papers before skipped ones and then the oldest skip;
the `/audit` page for a paper whose reading has a distinctive quote and rationale contains
neither (the blindness test); posting a verdict stores one audit entry and moves on;
a held-back paper is never served and its POST is 404; `/audit/disagreements` is 409
until every paper has a verdict; the disagreement page marks the quote in place and
escapes HTML in the abstract; an empty reason is 400; a decision removes the paper from
the list; a failed reading is not a disagreement; summary numbers for a small made-up
set, including that a decision changes `holds_final`; a POST with a foreign `Origin` is
403.

## Task 4: the concept card and the paper note

Extend `src/elusion/board/web.py` and add `src/elusion/board/card.py` (pure logic),
templates `card.html`, `paper.html`, `locked.html`, tests `tests/test_board_card.py` and
additions to `tests/test_board_web.py`. Tests first.

**The lock.** `GET /concept` and `GET /paper/<doc_id>` return status 423 with
`locked.html` ("finish the blind audit first", link to `/audit`) while any paper lacks a
blind verdict. A held-back paper's note is 404, and nothing of a held-back paper's
reading appears on the card. Add Concept to the nav line.

**`card.py`**

```python
def display_time(ts: datetime) -> str      # America/Los_Angeles, "2026-11-26 14:05 PST"
def field_month(publication_date: str) -> str
def field_history(papers: Iterable[Paper]) -> list[tuple[str, int]]
def mark_quotes(text: str, quotes: Sequence[str]) -> Markup
```

- `field_month`: `"2026-07"` for `2026-07-14`; a date on January 1 gives the year alone
  (`"2026"`), because that date usually means the month is not known.
- `field_history`: counts per `field_month` of the papers passed, sorted by the key.
- `mark_quotes`: the escaped text with each found quote wrapped in `<mark>`; spans that
  overlap are merged; a quote that is not found is ignored. The disagreement page of
  Task 3 is changed to use it.

**`GET /concept`**: the card, every statement carrying one of three source tags shown as
a small label: `paper` (a verbatim quote), `model` (a reading, with model and prompt
version), `decided` (a concept or decision entry, with actor and date).

1. The wording, its version, who wrote it and when (`decided`).
2. Counts: papers matching the words; held back; read; holds and does not hold by the
   reader; failed readings; then the audit's agreement and the precision of the word
   search with intervals from `audit.summary`.
3. Answers: one row per paper whose final verdict is `holds`: the reading's
   `detail["answer"]` (`model`), the quote (`paper`), title linked to its note, and its
   `field_month`. Where the reader said `does_not_hold` and the owner's final verdict is
   `holds`, the row says so and shows no answer.
4. Other meanings of the words: one row per paper whose final verdict is
   `does_not_hold`, with the reading's quote if any and its rationale.
5. The field's history: `field_history` of the papers whose final verdict is `holds`.
6. Our history: every entry of the concept in order, one line each, time by
   `display_time`: kind, actor or model, verdict, and for decisions the reason.

**`GET /paper/<doc_id>`**: title, work id, publication date; the abstract through
`mark_quotes` with the reading's quote; the reading (verdict, answer, rationale, model,
prompt version, and for a failed reading its `stop_reason` and the model's unverified
quote labelled as not verbatim); the owner's blind verdict; the decision and its reason
if any. Unknown id: 404.

**Tests**: `display_time` converts a UTC time across the date line (01:00 UTC is the
previous day in Pacific); `field_month` for a January 1 date and another date;
`mark_quotes` escapes, marks, merges overlaps, ignores a missing quote; `/concept` and
`/paper/<id>` are 423 before the blind step ends and 200 after; the card shows a holds
paper's quote and answer, lists a rejected paper under other meanings, and shows nothing
of a held-back paper; a paper whose decision overturned the blind verdict is listed by
its final verdict; the paper note of a failed reading shows the stop reason.
