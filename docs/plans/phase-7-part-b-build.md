# Phase 7 part B: build plan for the index, `ask` and `ask-eval`

Status: written 2026-11-26 from the approved spec and read by Claude before the build; the owner's steps are under "Needs the owner".

Spec: `docs/plans/phase-7-literature.md`, calls 9 to 13 and tasks 7 to 10 (all calls were
approved as recommended on 2026-11-16), and the DECISIONS lines of 2026-11-16 that mention
the index, vectors, embeddings and the gold questions. Where this plan and the spec differ,
the spec wins, except in the five places listed under "Where the spec could not be
followed as written".

What is built: a Postgres table of the screened-in abstracts, searchable by words and by
meaning; an `ask` command that answers one question from quoted passages and checks every
quote in Python; a gold set of 30 questions; and `ask-eval`, which compares three ways of
searching on those questions and logs one MLflow run per way.

Part B starts only after part A's report exists (it does: `docs/reports/auditable-decisions-gpt-6-luna-v1-2025-2026.md`).

## Terms, once

- An **embedding** is a list of numbers (a vector) that a model produces for a text, so
  that texts with similar meaning get nearby vectors. **Cosine similarity** is the
  measure of how nearby: 1 for the same direction, 0 for unrelated.
- **pgvector** is a Postgres extension that adds a `vector` column type and distance
  operators. An **exact scan** compares the question's vector with every stored vector.
  An ANN (approximate nearest neighbour) index skips most of them and can miss; this
  phase builds none.
- A **`tsvector`** is Postgres's form of a text reduced to its normalised words (stemmed,
  stop words dropped). A **full-text search** query ranks rows by how well their
  `tsvector` matches. It finds exact words and misses paraphrase.
- **Reciprocal rank fusion** (RRF) merges ranked lists: a document's score is the sum,
  over the lists, of `1 / (60 + its rank in that list)`. No score from one list is ever
  compared with a score from another.
- An **arm** is one way of searching: `lexical` (full-text), `vector`, or `hybrid` (RRF
  of the two).
- **recall@k**: the fraction of questions for which the one known source abstract is in
  the top k retrieved. A **passage** is one whole abstract; nothing is chunked.
- A **claim** is one sentence of an answer, with the id of the abstract and the quote it
  rests on. **Groundedness** is how many claims their quotes actually support.
- A **refusal** is the answer "the passages do not answer the question".

## Where the spec could not be followed as written

1. The spec says the index holds "a few hundred abstracts". The screened-in pile is
   10,577 (report, threshold 3), about 10,150 after the gold-drawn ones are held back.
   Exact scan and embedding cost are still fine (about 4 million tokens, cents). But
   recall@5 over 10,000 documents is a harder test than over a few hundred, and the
   DECISIONS line of 2026-11-16 repeats the "few hundred" figure. Task E's DECISIONS line
   corrects it.
2. The spec says the screened-in set is defined by scores alone. The project hard rule
   says the gold set is never in a retrieval context. Both hold if the set is "score at
   or above the threshold, minus the ids in `data/gold/`" (the gold CSV and the judge
   refusals file, read through `board.batch.held_back_ids`). Labels are never read. The
   owner confirms this in "Needs the owner", step 3, before the first paid run.
3. Spec task 8 says "ask uses the arm that wins", but the winner is known only after
   `ask-eval`. Until then `DEFAULT_ARM = "hybrid"`; Task E changes that one constant.
4. The spec's call 10 says the hybrid is one SQL statement. This plan fuses in Python
   (a pure function) so the fusion can be unit tested without a database. The two arms
   inside it are still SQL.
5. `claude-sonnet-5-5` and `text-embedding-3-small` have no row in
   `src/elusion/review/pricing.py`, and no price may be guessed. The rows are added after
   the facts under "To confirm before the first paid call" are read.

## To confirm before the first paid call

The builder reads the provider's own documentation (OpenAI's models and pricing pages,
Anthropic's models and pricing pages), writes each fact in a DECISIONS line with the page
and the date, and only then fills the constants below. If the builder cannot read the
pages, it stops and tells the owner; it does not fill them from memory. Unit tests never
need these values: they use a free fake model.

1. OpenAI `text-embedding-3-small`: the exact model id string, the price per million input
   tokens, the default vector length (`EMBEDDING_DIM`; the code has the placeholder 1536
   and the builder must check it), the maximum input tokens of one text, and the maximum
   texts per request.
2. Anthropic `claude-sonnet-5-5`: the exact id, price per million input, output, cache
   write and cache read tokens, and whether `output_config` with `effort` and a JSON
   schema `format` is accepted (the existing provider sends both).
3. Anthropic `claude-opus-5-5`: the price row already in `pricing.py` (4.00 input, 20.00
   output, 5.00 cache write, 0.20 cache read) read against the current page, and the same
   `output_config` question.
4. Whether the Anthropic SDK counts adaptive thinking tokens against `max_tokens` for
   these two models (this decides `ANSWER_MAX_TOKENS` in Task C).

## Global Constraints

- Python 3.12, `uv`, `ruff` (`uv run ruff check`, `uv run ruff format`), `pytest`. Follow
  the existing modules: SQLAlchemy Core with `psycopg`, a module docstring that explains
  the idea in plain words, flows in `src/elusion/flows/` written like
  `flows/first_read.py`, tests that skip when Postgres is not reachable.
- No new dependency. In particular not the `pgvector` Python package (a new package needs
  the owner's yes). Task A writes a 30-line SQLAlchemy type instead. `openai` and
  `anthropic` are already declared.
- Precondition, done on 2026-11-26 before Task A: the `postgres` service in
  `compose.yaml` runs `pgvector/pgvector:0.8.2-pg16-trixie` on the same data volume, after
  a `pg_dump` to `data/backups/elusion-2026-11-26-before-pgvector.dump`. The code runs
  `CREATE EXTENSION IF NOT EXISTS vector` in its own `ensure_schema`; nobody runs it by
  hand. Nothing in `infra/` changes. Everything runs locally from `compose.yaml`.
- Builders never make a paid call and never run `op`. Every test uses a fake and no
  network. Paid runs are the owner's, listed under "Needs the owner". Builders never read
  `.env` files.
- Every model call (embedding, answer, groundedness judge, question draft) goes out with
  a pinned model id from `src/elusion/ask/models.py` and a prompt version string, and
  both are stored with the result. The id the API returns is stored beside the pinned
  one. An embedding has no prompt, so only its model id is stored.
- Prompt files are never edited once a call has used them; a change is `v2.md`.
- Idempotence keys, exact:
  - a vector: (`doc_id`, `embedding_model`)
  - an index row: `doc_id`
  - an eval result: (`question`, `arm`, `answer_model`, `prompt_version`)
  - a claim judgement: (`ask_id`, `claim_index`, `judge_model`, `judge_prompt_version`)
  - a drafted question: its `source_doc_id` in the draft file
  A re-run finds the key present and makes no call. A call that returned no usable answer
  still holds its key (it was billed), as in `first_read`.
- Spend guard: `index`, `ask-draft` and `ask-eval` project their cost before the first
  call and raise `elusion.flows.review.BudgetExceeded` (reuse it, do not define another)
  when it is above `ELUSION_MAX_RUN_USD` (`settings.max_run_usd`, default 10). The message
  names both figures, as `first_read` does. A single `ask` is not guarded.
- The gold rule. Nothing from `data/gold/` enters a prompt, the index or a retrieval
  context: no label, no judge verdict, no held-back abstract. A gold question's source
  document id appears only in the questions file, in the eval result row and in the test
  that compares it with the retrieved ids. It is never in a prompt, never in the index
  tables, never passed to `retrieve` or `ask`. (The source abstract's own text is in the
  index like any other abstract; that is what makes the question answerable.)
- Anti-goals from the spec, binding: no agent (`ask` retrieves once and answers once, no
  planning, no rewriting the question, no loop); no reranking model; no chunking; no full
  texts; no ANN index; no UI; no service that stays up; no change to any 10-K score or
  report; no abstract text in git (reports list works by id and title only); no email
  address or key in git.
- Exact words. Arms: `lexical`, `vector`, `hybrid`. Outcomes of an ask: `answered`,
  `refused`, `failed`, `error`. Prompt version strings: `ask/v1` (answer),
  `ask/judge-v1` (groundedness judge), `ask/draft-v1` (question draft). The refusal text
  printed by `ask`, exactly: `The passages do not answer the question.`
- Vocabulary stays "flagged", "discard pile", "screened-in" (flagged at or above the
  threshold). No renames.
- Timestamps are `timestamptz`, stored in UTC. A date printed in a report goes through
  `flows.evaluate.report_date`.
- Tests never touch the compose database's real tables: they use the `index_engine`
  fixture of Task A (a throwaway database).
- Commit at the end of each task on the current branch `phase-7-part-b`. The message ends
  with the `Co-Authored-By:` line for the model that wrote the change. Do not push.
- Each task is done when `uv run ruff check`, `uv run ruff format --check` and
  `uv run pytest` pass (the whole suite, not only the new tests) and the task's own
  tests are named as listed.

Order: A, B, C, D1, D2, E. Each task builds on the one before and hands the next one a
named thing. Review each before starting the next.

## Task A: the index

Create `src/elusion/ask/__init__.py` (empty), `src/elusion/ask/models.py`,
`src/elusion/ask/embed.py`, `src/elusion/ask/index.py`, `src/elusion/flows/index.py`,
`tests/test_ask_index.py`, `tests/test_embed.py`, `tests/test_index_flow.py`. Change
`tests/conftest.py` (add `index_engine`), `tests/fakes.py` (add `FakeEmbedder`),
`src/elusion/review/pricing.py` (rows, after the confirmations) and `src/elusion/cli.py`
(the `index` command, tests in `tests/test_cli.py`). Tests first.

**`models.py`**: constants only.

```python
EMBEDDING_MODEL = "text-embedding-3-small"   # confirm the id
EMBEDDING_DIM = 1536                          # confirm; the vector column is made from this
ANSWER_MODEL = "claude-sonnet-5-5"            # confirm the id
JUDGE_MODEL = "claude-opus-5-5"
DRAFT_MODEL = "claude-opus-5-5"
```

Each confirmed constant has a comment `confirmed from <page> on <date>`. A builder that
has not read the page leaves the comment as `NOT CONFIRMED` and says so in its report.

**`index.py`**: two tables in their own `MetaData`, SQLAlchemy Core.

`abstract_index` (one row per screened-in abstract):
- `doc_id` String(16) primary key
- `work_id` Text not null, `title` Text not null (empty string when none),
  `publication_date` Text not null, `text` Text not null
- `tsv` TSVECTOR, `sa.Computed("to_tsvector('english', title || ' ' || text)",
  persisted=True)`, with a GIN index named `ix_abstract_index_tsv`
- `review_model` Text not null, `review_prompt_version` Text not null,
  `threshold` SmallInteger not null (the pair and threshold that screened it in)
- `indexed_at` DateTime(timezone=True) not null, server default `now()`

`abstract_vectors`:
- `doc_id` String(16) not null, foreign key to `abstract_index.doc_id` `ON DELETE CASCADE`
- `embedding_model` Text not null; primary key (`doc_id`, `embedding_model`)
- `embedding` Vector(`EMBEDDING_DIM`) not null
- `embedded_at` DateTime(timezone=True) not null, server default `now()`
- no index on `embedding`

The `Vector` type is a `sa.types.UserDefinedType` with `cache_ok = True`; `get_col_spec`
returns `vector(<dim>)`; the bind processor turns a list of floats into the text
`[0.1,0.2,...]` (use `repr(float(x))`); `bind_expression` wraps the parameter in
`sa.cast(bindvalue, self)`; the result processor parses the text back to `list[float]`.
Builder's choice: no `pgvector` package, because a new package needs the owner's yes and
30 lines do the job.

Functions (all take `engine` first):

```python
class IndexPairMismatch(ValueError): ...

def ensure_schema(engine) -> None
def document_text(title: str, text: str) -> str
def screened_in_ids(engine, review_model: str, prompt_version: str, threshold: int = 3) -> list[str]
def indexed_pair(engine) -> tuple[str, str, int] | None
def add_abstracts(engine, papers: Iterable[Paper], *, review_model: str,
                  prompt_version: str, threshold: int) -> int
def drop_abstracts(engine, doc_ids: Iterable[str]) -> int
def unembedded(engine, embedding_model: str) -> list[tuple[str, str]]
def add_vectors(engine, embedding_model: str, vectors: Mapping[str, Sequence[float]]) -> int
def count_indexed(engine) -> int
def count_vectors(engine, embedding_model: str) -> int
```

- `ensure_schema`: `CREATE EXTENSION IF NOT EXISTS vector`, then `metadata.create_all`.
  Safe to repeat.
- `document_text(title, text)`: `f"{title}\n\n{text}"` cut to `EMBED_MAX_CHARS = 20_000`
  characters. This is the text that is embedded. (Builder's choice: title first, because
  it carries the topic in few words; the cap keeps a text under the model's token limit
  at about three characters a token. The `tsvector` uses the full text.)
- `screened_in_ids`: `review.db.scores_for(engine, review_model, prompt_version)` (the
  full pair key, e.g. `auditable-decisions/v1`), keep `score >= threshold`, sorted. This
  is the only place the set is defined. It reads no judge label and no gold file.
- `indexed_pair`: the single (`review_model`, `review_prompt_version`, `threshold`) in
  the table, `None` when empty; `IndexPairMismatch` when rows disagree.
- `add_abstracts`: `ON CONFLICT DO NOTHING` on `doc_id`, returns rows inserted
  (`RETURNING`, not `rowcount`, as `upsert_documents` does).
- `unembedded`: (`doc_id`, `document_text(title, text)`) of every row with no vector for
  `embedding_model`, in `doc_id` order.
- `add_vectors`: `ON CONFLICT DO NOTHING`; a vector whose length is not `EMBEDDING_DIM`
  raises `ValueError` naming the doc id and both lengths, before anything is inserted.

**`embed.py`**:

```python
BATCH = 100

@dataclass(frozen=True)
class Embedded:
    vectors: list[list[float]]   # in the order of the input texts
    model: str                   # as served by the API
    input_tokens: int
    cost_usd: float

@runtime_checkable
class Embedder(Protocol):
    name: str
    model: str
    def embed(self, texts: Sequence[str]) -> Embedded: ...

class OpenAIEmbedder:
    name = "openai"
    def __init__(self, model: str = EMBEDDING_MODEL, client: openai.OpenAI | None = None)
```

`embed` makes one `client.embeddings.create(model=self.model, input=list(texts))` call,
orders the vectors by each item's `index`, takes tokens from `usage.prompt_tokens`, prices
with `cost_usd(self.model, tokens, 0)`. An empty list makes no call. Do not pass
`dimensions`. The client is built like `OpenAIProvider`'s (`MAX_RETRIES`,
`REQUEST_TIMEOUT_S` from `review.providers`). With no `OPENAI_API_KEY` in the environment
the constructor raises `ProviderConfigError` with the same message `make_provider` uses.

**`flows/index.py`**: a Prefect flow `build_index`.

```python
def build_index(criterion: str, embedder: Embedder, *, review_model: str,
                prompt_version: str = "v1", threshold: int = 3, engine=None,
                abstracts_dir=None, gold_csv=None, max_usd: float | None = None) -> dict
```

1. `ensure_schema`. `corpus_for(criterion)` must be `OPENALEX`, else `ValueError`
   ("ask searches abstracts only").
2. `wanted = screened_in_ids(engine, review_model, f"{criterion}/{prompt_version}",
   threshold)`. Empty: `ValueError` naming the pair (a typo guard).
3. `held = held_back_ids(gold_csv or gold_path(criterion, settings.data_dir))`.
4. `indexed_pair(engine)` must be `None` or equal to the pair and threshold of this run,
   else `IndexPairMismatch`.
5. `papers = load_papers(abstracts_dir or OPENALEX.text_dir(settings.data_dir))`, kept
   when in `wanted - held`. Ids in `wanted - held` with no paper in Parquet are counted
   as `no_text` and skipped.
6. `drop_abstracts` for index rows in `held` (a gold set that grew after the last run);
   `add_abstracts` for the papers.
7. `todo = unembedded(engine, embedder.model)`. Projected cost is the sum over `todo` of
   `cost_usd(embedder.model, len(text) // 3 + 1, 0)`. Above `max_usd` (default
   `settings.max_run_usd`): raise `BudgetExceeded` before any call.
8. In batches of `BATCH`: `embedder.embed`, `add_vectors`. Each batch is stored before the
   next is sent, so a crash keeps every finished batch.
9. Return `{"screened_in", "held_back_removed", "no_text", "rows_added",
   "already_embedded", "embedded", "input_tokens", "cost_usd"}`.

**CLI**: `elusion index --criterion criteria/auditable-decisions.md --prompt-version v1
--review-model gpt-6-luna [--threshold 3] [--max-usd N]`, option names as in `judge`. It
builds `OpenAIEmbedder()` and prints the summary. `BudgetExceeded`, `IndexPairMismatch`,
`ProviderConfigError`, `UnknownCriterion` and `ValueError` print `error: ...` and exit 1
without a traceback, as `first-read` does.

**`conftest.py::index_engine`** (function scope). Same method as `board_engine`:
`CREATE DATABASE elusion_test_<8 hex>` on the compose Postgres with AUTOCOMMIT, an engine
on it, then `ask.index.ensure_schema`, `review.db.ensure_schema` and (Task C) the ask
tables' `ensure_schema`, then `DROP DATABASE ... WITH (FORCE)`. It skips when
localhost:5433 is not reachable. `CREATE EXTENSION vector` happens inside
`index.ensure_schema`, so the fixture is the proof that the code, not a person, creates
the extension. If the server has no pgvector, the test fails with Postgres's own message
(do not skip it: a silent skip would hide a wrong image). Builder's choice: function
scope, because the index flow tests need different pairs and the cost of a fresh database
is a fraction of a second.

**`FakeEmbedder`** in `tests/fakes.py`: `FakeEmbedder(model="fake-embedder",
dim=EMBEDDING_DIM, fail_on_call=None)`. It calls `register_free(model)`. Each text becomes
a vector of word counts, the word hashed with `zlib.crc32(word.lower()) % dim` (not
Python's `hash`, which changes between runs), scaled to length 1. `calls` records every
list of texts it was sent. `fail_on_call=2` raises `RuntimeError` on its second call.

**Tests**:
- `test_ask_index.py`: `test_ensure_schema_creates_the_extension`,
  `test_ensure_schema_twice`, `test_vector_round_trip`,
  `test_add_vectors_refuses_a_wrong_length_and_inserts_nothing`,
  `test_add_abstracts_is_idempotent`, `test_tsv_is_built_from_title_and_text` (a word only
  in the title matches), `test_no_index_on_the_embedding_column` (`pg_indexes` for
  `abstract_vectors` lists only the primary key), `test_unembedded_lists_rows_without_a_vector_for_that_model`,
  `test_vectors_are_kept_per_model`, `test_drop_abstracts_removes_their_vectors`,
  `test_document_text_is_capped`, `test_indexed_pair`.
- `test_embed.py`: `test_vectors_come_back_in_input_order` (stub client returns items out
  of order), `test_tokens_and_cost` (cost equals `cost_usd(model, tokens, 0)`),
  `test_empty_input_makes_no_call`, `test_missing_key_is_a_config_error`.
- `test_index_flow.py` (with the `prefect_test_harness` fixture as in
  `test_first_read_flow.py`; seed `scores` rows through `review.db.insert_scores`, texts
  through `tests.test_board_batch.write_year`):
  `test_only_scores_at_or_above_the_threshold_are_indexed`,
  `test_a_gold_label_does_not_change_the_set` (the same flagged id with gold label 0 and
  with label 1 gives the same index),
  `test_held_back_ids_are_not_indexed_and_are_removed_if_already_there`,
  `test_a_second_run_embeds_nothing` (the fake's `calls` stays empty),
  `test_a_new_embedding_model_embeds_everything_and_keeps_the_old_vectors`,
  `test_spend_guard_raises_before_any_call`,
  `test_a_crash_in_batch_two_keeps_batch_one` (`monkeypatch` `BATCH` to 2; the rerun
  embeds only the rest), `test_a_different_pair_is_refused`,
  `test_the_10k_criterion_is_refused`, `test_papers_without_text_are_counted_and_skipped`.
- `test_cli.py`: `test_index_command_passes_the_pair_and_prints_the_summary`,
  `test_index_errors_exit_1_without_a_traceback`.

**What must not happen**: a judge label or a gold CSV label read for any purpose except
the held-back id list; an index built from scores of another pair; a second embedding call
for a (doc, model) that has a vector; an index on `embedding`; a price guessed.

**Hands to Task B**: tables `abstract_index` and `abstract_vectors`, `Embedder`,
`FakeEmbedder`, `index_engine`, `EMBEDDING_DIM`.

Builder's choice: GIN index on `tsv`, because it is Postgres's ordinary exact full-text
index and, unlike an ANN index, never approximates.

## Task B: the three arms

Create `src/elusion/ask/retrieve.py` and `tests/test_ask_retrieve.py`. Tests first.

```python
K = 5
RRF_C = 60
FUSION_DEPTH = 50
ARMS = ("lexical", "vector", "hybrid")
DEFAULT_ARM = "hybrid"

@dataclass(frozen=True)
class Hit:
    doc_id: str
    rank: int       # 1 is best
    score: float

@dataclass(frozen=True)
class Passage:
    doc_id: str
    title: str
    text: str

def lexical(engine, question: str, k: int = K) -> list[Hit]
def vector(engine, query_vector: Sequence[float], embedding_model: str, k: int = K) -> list[Hit]
def rrf(rankings: Sequence[Sequence[str]], k: int = K, c: int = RRF_C) -> list[Hit]
def hybrid(engine, question: str, query_vector: Sequence[float], embedding_model: str,
           k: int = K, depth: int = FUSION_DEPTH) -> list[Hit]
def retrieve(engine, arm: str, question: str, query_vector: Sequence[float] | None,
             embedding_model: str | None, k: int = K) -> list[Hit]
def passages(engine, doc_ids: Sequence[str]) -> dict[str, Passage]
```

None of these makes a model call: the caller embeds the question and passes the vector in.

- `lexical`: one query. Builder's choice: join the question's words with OR, not AND,
  because a natural-language question has words the right abstract lacks, and
  `plainto_tsquery` would then match nothing. Exact SQL:
  `WITH q AS (SELECT to_tsquery('english', replace(plainto_tsquery('english', :question)::text, '&', '|')) AS query) SELECT doc_id, ts_rank_cd(tsv, q.query) AS score FROM abstract_index, q WHERE tsv @@ q.query ORDER BY score DESC, doc_id LIMIT :k`.
  It returns fewer than `k` hits when fewer match, and `[]` when no word matches or the
  question is all stop words.
- `vector`: `WHERE embedding_model = :m ORDER BY embedding <=> CAST(:q AS vector) , doc_id
  LIMIT :k`, score `1 - (embedding <=> q)` (cosine similarity). `<=>` is pgvector's cosine
  distance. A `query_vector` of the wrong length raises `ValueError` before any query.
  Vectors of another embedding model are invisible.
- `rrf`: ranks are 1-based within each list, a document absent from a list adds nothing
  for it, score is the sum of `1 / (c + rank)`, ties broken by `doc_id`, top `k`, ranks
  1-based. Builder's choice: fuse in Python, not in SQL, so the fusion is tested with no
  database.
- `hybrid`: `lexical(depth)` and `vector(depth)`, then `rrf` of their id lists.
  `FUSION_DEPTH = 50` is Builder's choice: deep enough that a document ranked 20th by one
  arm and 3rd by the other still fuses, shallow enough to stay cheap.
- `retrieve`: dispatch by arm. `vector` and `hybrid` with a `None` vector or model:
  `ValueError`. An arm not in `ARMS`: `ValueError` listing the three.
- `passages`: title and text of the rows, for the ids given.

**Choosing k.** `K = 5`, decided now, before any gold question exists as a file or has
been looked at. Builder's choice and why: `ask` hands the answer model k whole abstracts,
so k = 5 is about 2,000 to 2,500 tokens, small enough that the owner can read every
retrieved abstract by hand when an answer looks wrong, and cheap enough for 90 eval asks.
It is also a strict test: with about 10,000 abstracts, a right abstract in the top 5 is
real evidence that the arm found it. The task appends this DECISIONS line (dated the day
it is written, today's date at that time): "k is 5 for recall@k and for what `ask` reads,
fixed before the gold questions were drafted or looked at; ask-eval has no flag to change
it." `ask-eval` has no `--k` option (Task D2).

**Tests** (with `index_engine`, `FakeEmbedder` vectors for the rows, hand-made vectors
where a cosine order must be exact): `test_k_is_five`, `test_lexical_finds_a_word`,
`test_lexical_stems` (the question says "auditing", the abstract "audit"),
`test_lexical_matches_when_one_question_word_is_absent` (the OR rule),
`test_lexical_returns_fewer_than_k_when_few_match`,
`test_lexical_all_stop_words_returns_nothing`,
`test_lexical_odd_punctuation_does_not_error` (`"What is AT&T's 'audit' (trail)?"`),
`test_lexical_order_is_by_score_then_doc_id`, `test_vector_orders_by_cosine`,
`test_vector_scores_are_cosine_similarity`, `test_vector_ignores_other_models_vectors`,
`test_vector_wrong_dimension_raises`, `test_rrf_worked_example` (lists `[a, b, c]` and
`[b, a, d]`: a and b tie at `1/61 + 1/62`, so `a` comes first by id; `c` and `d` get
`1/63` and `1/63`), `test_rrf_a_document_in_both_lists_beats_one_at_rank_one_in_one`,
`test_rrf_ties_break_by_doc_id`, `test_hybrid_returns_a_doc_only_lexical_finds_and_one_only_vector_finds`,
`test_hybrid_is_deterministic`, `test_retrieve_dispatches`,
`test_retrieve_vector_without_a_vector_raises`, `test_retrieve_unknown_arm_raises`,
`test_passages_returns_title_and_text`.

**What must not happen**: a model call; a gold id or label as an argument or filter; a
filter on anything but `embedding_model`.

**Hands to Task C**: `Hit`, `Passage`, `retrieve`, `passages`, `K`, `ARMS`, `DEFAULT_ARM`.

## Task C: `ask`

Create `src/elusion/ask/answer.py`, `src/elusion/ask/log.py`,
`src/elusion/review/prompts/ask/v1.md`, `tests/test_ask_answer.py`,
`tests/test_ask_log.py`. Change `src/elusion/review/providers.py`, `tests/fakes.py`,
`tests/test_providers.py`, `src/elusion/review/pricing.py` (the Sonnet row, after
confirmation), `tests/conftest.py` (`index_engine` also runs `ask.log.ensure_schema`) and
`src/elusion/cli.py` (the `ask` command, tests in `tests/test_cli.py`). Tests first.

**`providers.py`** gains one generic structured call, used by this task, D1 and D2.
Builder's choice: one `complete` method and one `Completion` dataclass, not three more
near-identical dataclasses and Protocols for answer, judge and draft.

```python
@dataclass
class Completion(Generic[_Out]):
    parsed: _Out
    model: str            # as served by the API
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

@runtime_checkable
class Completer(Protocol):
    name: str
    model: str
    def complete(self, system: str, message: str, out: type[_Out], *,
                 max_tokens: int = MAX_TOKENS) -> Completion[_Out]: ...
```

`AnthropicProvider.complete` builds `transform_schema(out.model_json_schema())` and goes
through `_call`. `_call` gains one keyword, `max_tokens: int = MAX_TOKENS`, passed to
`messages.create`; nothing else in it changes and the existing callers are untouched.
Unusable answers still raise `ReviewError` with usage. Tests in `test_providers.py`, in
the style already there: `test_complete_returns_the_parsed_model_and_usage`,
`test_complete_passes_max_tokens`, `test_complete_raises_review_error_on_truncation`.

**`FakeCompleter`** in `tests/fakes.py`: `FakeCompleter(answer, model="fake-completer")`
where `answer(system, message, out)` returns an instance of `out` or an exception (raised).
`calls` records (system, message). Returns `Completion` with 100 input tokens, 20 output
tokens, cost 0.0. It calls `register_free(model)`.

**Prompt file `ask/v1.md`**, exactly this text:

```
You answer one question using only the passages given. Each passage is one paper's abstract, labelled with a document id in square brackets.

You are given the question after QUESTION: and the passages after PASSAGES:. Use nothing you know from elsewhere. Answer with these fields:

- answerable: true if the passages answer the question, false if they do not. If they do not, say so: do not guess, and do not answer from what you know.
- claims: if answerable is true, a list of one to four claims that together answer the question; otherwise an empty list. Each claim has:
  - claim: one sentence, in your own words, that the quote below supports.
  - doc_id: the document id of the passage the quote comes from, exactly as written in the square brackets.
  - quote: the one passage of that abstract that supports the claim, copied character for character from the abstract text: no ellipsis, no corrections, no words left out, nothing joined from two places. Do not quote the title. Keep it under 40 words.

A claim that no passage supports must not be made. A claim rests on one quote only.
```

**`answer.py`**:

```python
ANSWER_MAX_TOKENS = 4096
PROMPT = "ask"
REFUSAL = "The passages do not answer the question."

class Claim(BaseModel):   # extra="forbid"; each field has a description
    claim: str
    doc_id: str
    quote: str

class Answer(BaseModel):  # extra="forbid"
    answerable: bool
    claims: list[Claim]

@dataclass(frozen=True)
class ClaimCheck:
    index: int
    doc_id: str
    retrieved: bool   # the cited id is among the passages the model was given
    verbatim: bool    # find_quote finds the quote in that abstract's text (not its title)

@dataclass(frozen=True)
class AskResult:
    ask_id: int
    outcome: str
    answer: Answer | None
    checks: list[ClaimCheck]
    retrieved: list[Hit]
    cost_usd: float

def ask_message(question: str, passages: Sequence[Passage]) -> str
def check_answer(answer: Answer, texts: Mapping[str, str]) -> list[ClaimCheck]
def ask(engine, question: str, answerer: Completer, embedder: Embedder | None, *,
        arm: str = DEFAULT_ARM, k: int = K, embedding_model: str = EMBEDDING_MODEL,
        prompt_version: str = "v1") -> AskResult
```

`ask_message` returns exactly `f"QUESTION:\n{question}\n\nPASSAGES:\n{blocks}"` where
`blocks` is `"\n\n".join(f"[{p.doc_id}] {p.title}\n{p.text}" for p in passages)`, in rank
order.

`check_answer`: `texts` maps the retrieved ids to their abstract text. For each claim,
`retrieved = claim.doc_id in texts`; `verbatim = retrieved and find_quote(texts[doc_id],
claim.quote) is not None` (reuse `elusion.board.quotes.find_quote`; whitespace runs are
the only tolerance; an empty quote fails).

`ask`, in order:
1. An arm not in `ARMS`: `ValueError`. An arm that needs a vector with `embedder` None:
   `ValueError`.
2. For `vector` and `hybrid`, `embedder.embed([question])` once. Its tokens and cost are
   kept for the log row.
3. `retrieve`, then `passages`.
4. No hits (a lexical question with no matching word): no model call. Write a row with
   outcome `refused`, `answer` null, `served_model` null, cost of the embedding only
   (zero for lexical), `detail["no_passages"] = true`. Return.
5. One `answerer.complete(prompt.text, ask_message(...), Answer, max_tokens=ANSWER_MAX_TOKENS)`.
   `load_prompt("ask", prompt_version)` supplies the system text and the stored version
   string `ask/v1`. Builder's choice for the 4096: adaptive thinking tokens may count
   against `max_tokens`, and a cut-off answer is a wasted ask (see "To confirm", item 4).
6. `ReviewError`: outcome `error`, no answer, `checks` `[]`, `stop_reason` and the billed
   tokens and cost stored. Not retried.
7. Otherwise: `answerable` false with no claims is `refused`. `answerable` true with no
   claims, or `answerable` false with claims, is `failed` with `detail["inconsistent_answer"]
   = true`. Otherwise run `check_answer`; all claims retrieved and verbatim is `answered`,
   anything else is `failed`. A failed ask is stored whole and reported as failed: no
   retry, no dropped claims, no second call to fix the quote.
8. One `add_ask` row. Cost is the answer's cost plus the query embedding's; the embedding
   part is also in `detail["embedding"] = {"model", "input_tokens", "cost_usd"}`.
9. `answer_model` on the row is `answerer.model` (pinned); `served_model` is what the API
   returned. A mismatch is logged as a warning, as `first_read` does.

**`log.py`**: table `ask_log`, append-only, written like `board/log.py` (same
`_REFUSE_FUNCTION` approach: function `ask_log_refuse_change()` raising `ask_log is
append-only`, a row trigger `BEFORE UPDATE OR DELETE`, a statement trigger `BEFORE
TRUNCATE`).

- `ask_id` BigInteger identity primary key
- `asked_at` DateTime(timezone=True) not null, server default `now()`
- `question` Text not null (exactly as typed; no strip)
- `arm` Text not null, CHECK in the three arms; `k` SmallInteger not null
- `retrieved` JSONB not null: `[{"doc_id", "rank", "score"}, ...]` in rank order
- `embedding_model` Text null (null for `lexical`)
- `answer_model` Text not null; `served_model` Text null; `prompt_version` Text not null
- `outcome` Text not null, CHECK in the four outcomes
- `answer` JSONB null (the `Answer` as parsed, whole)
- `checks` JSONB not null default `[]`: `[{"index", "doc_id", "retrieved", "verbatim"}]`
- `stop_reason` Text null
- `input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens` Integer not null default 0
- `cost_usd` Numeric(10, 6) not null default 0
- `detail` JSONB not null default `{}`

Functions: `ensure_schema(engine)`, `add_ask(engine, **columns) -> int`,
`get_ask(engine, ask_id) -> AskRow` (frozen dataclass, one field per column),
`asks(engine, question: str | None = None) -> list[AskRow]` in `ask_id` order. This table
is the audit trail of FIELD-NOTES section 2, steps 1, 3 and 4: who asked and the exact
wording; what was returned; the decision and its quote; the model and prompt version.

**CLI**: `elusion ask "QUESTION" [--arm hybrid] [--k 5] [--model claude-sonnet-5-5]`
(QUESTION is a positional argument). It builds `AnthropicProvider(model)` and, unless the
arm is `lexical`, `OpenAIEmbedder()`. Output, exactly:
- an `answered` or `failed` ask: first line `ask <ask_id>: <outcome>, arm <arm>, k <k>,
  $<cost to 4 places>`; then per claim `<n>. <claim>`, then `   [<doc_id>] "<quote>"`, then
  `   check: ok`, or `   check failed: not among the retrieved passages`, or `   check
  failed: quote not found in the abstract`.
- a `refused` ask: the first line, then `The passages do not answer the question.`
- an `error` ask: `error: the model gave no usable answer (stop_reason=<x>); ask <id>
  holds the cost`.
Exit code 0 for `answered` and `refused`, 1 for `failed` and `error`. `ValueError`,
`ProviderConfigError` print `error: ...` and exit 1.

**Tests** (`index_engine`, `FakeEmbedder`, `FakeCompleter`):
`test_the_model_gets_the_prompt_file_and_exactly_the_template_message`,
`test_a_good_answer_is_answered_and_every_field_is_logged` (question with trailing
spaces kept, arm, k, retrieved ranks and scores, pinned and served model, `ask/v1`,
answer, checks, tokens, cost),
`test_a_quote_not_in_the_abstract_fails_and_the_model_is_called_once`,
`test_a_quote_differing_only_in_whitespace_passes`, `test_a_quote_of_the_title_fails`,
`test_a_cited_id_in_the_index_but_not_retrieved_fails`,
`test_a_cited_id_not_in_the_index_fails`, `test_a_refusal_is_stored_with_no_claims`,
`test_answerable_true_with_no_claims_fails`, `test_answerable_false_with_claims_fails`,
`test_a_review_error_stores_an_error_row_with_its_cost`,
`test_no_hits_makes_no_model_call_and_is_a_refusal`,
`test_the_vector_arm_embeds_the_question_once_and_adds_its_cost`,
`test_the_lexical_arm_makes_no_embedding_call`,
`test_a_vector_arm_without_an_embedder_raises`, `test_an_unknown_arm_raises`,
`test_pinned_and_served_model_are_both_stored`; in `test_ask_log.py`:
`test_update_is_refused`, `test_delete_is_refused`, `test_truncate_is_refused`,
`test_ensure_schema_twice`, `test_an_unknown_outcome_is_refused_by_the_database`,
`test_written_at_is_timezone_aware`; in `test_cli.py`: `test_ask_prints_claims_and_checks`,
`test_ask_refusal_text_is_exact`, `test_failed_ask_exits_1`,
`test_ask_lexical_builds_no_embedder`.

**What must not happen**: a second model call for one ask; a repaired or trimmed answer;
a gold or judge label anywhere in the message; any search of the corpus beyond the one
retrieval; an `UPDATE` of `ask_log`.

**Hands to Task D1**: `ask`, `AskResult`, `Answer`, `Claim`, `ClaimCheck`, `Completer`,
`FakeCompleter`, the `ask_log` table.

Builder's choices: (a) `ask_log` is append-only, like `concept_log`, because it is an
audit trail and the database should refuse edits, not just the code. (b) A question with
no retrieved passage is a refusal with no model call, because a call on empty input is
paid for nothing; the row says so in `detail`. (c) `ask` is a plain function, not a
Prefect flow: it is one call with nothing to resume. `index` and `ask-eval` are flows.

## Task D1: the gold questions

Create `src/elusion/ask/questions.py`, `src/elusion/review/prompts/ask/draft-v1.md`,
`tests/test_ask_questions.py`. Change `src/elusion/cli.py` (commands `ask-draft` and
`ask-questions`, tests in `tests/test_cli.py`). Tests first.

The question set is 30 questions: 25 that a known screened-in abstract answers, and 5 that
the corpus cannot answer. Opus drafts one question from each of 40 abstracts; the owner
strikes or rewrites down to 25 and writes the 5 unanswerable ones by hand.

**Prompt file `ask/draft-v1.md`**, exactly this text:

```
You write one question that a researcher could put to a literature search, answerable from one paper's abstract.

You are given the abstract after ABSTRACT:. Write one question that the abstract answers. Rules:

- The question must make sense to someone who has not seen the abstract: do not say "the paper", "this study" or "the authors".
- Do not copy distinctive words or phrases from the abstract. Say the same thing in other words, as a researcher who has not read it would put it.
- The question must be about something the abstract states, not something it only hints at.
- One sentence, ending in a question mark.

Answer with the field question.
```

**`questions.py`**:

```python
DRAFT_N = 40
ANSWERABLE_N = 25
UNANSWERABLE_N = 5
QUESTION_SEED = 20261126

class Draft(BaseModel):          # extra="forbid"
    question: str

@dataclass(frozen=True)
class Question:
    question_id: str
    kind: str                    # "answerable" or "unanswerable"
    source_doc_id: str           # "" for unanswerable
    question: str
    origin: str                  # "drafted", "rewritten" or "owner"
    draft_model: str
    draft_prompt_version: str

def draft_path(criterion: str, data_dir: Path) -> Path      # data/gold/<criterion>.questions-draft.csv
def questions_path(criterion: str, data_dir: Path) -> Path  # data/gold/<criterion>.questions.csv
def draft_message(text: str) -> str                         # f"ABSTRACT:\n{text}"
def draw_sources(engine, n: int = DRAFT_N, seed: int = QUESTION_SEED) -> list[str]
def draft_questions(engine, drafter: Completer, path: Path, *, n: int = DRAFT_N,
                    seed: int = QUESTION_SEED, max_usd: float | None = None) -> dict
def finalize_questions(draft_csv: Path, out_csv: Path, *, now: datetime | None = None) -> dict
def read_questions(path: Path) -> list[Question]
```

The draft file columns, in order: `question_id`, `kind`, `source_doc_id`,
`drafted_question`, `question`, `keep`, `draft_model`, `draft_prompt_version`,
`drafted_at`. The final file columns: `question_id`, `kind`, `source_doc_id`, `question`,
`origin`, `draft_model`, `draft_prompt_version`, `finalized_at`. Both are CSV written with
the `csv` module, UTF-8, ISO 8601 UTC timestamps, files under `data/gold/` (gitignored,
beside the other gold files). Neither file is read by `held_back_ids` or any existing
code, so the gold CSV and the judge-refusals file keep their meaning.

- `draw_sources`: the sorted `doc_id`s in `abstract_index`, then
  `random.Random(seed).sample(ids, n)`. Fewer than `n` rows: `ValueError`. Because the
  index already leaves out the held-back ids, no drafted source is a gold-labelled
  abstract.
- `draft_questions`: for each drawn id, in draw order, whose id is not already a
  `source_doc_id` in the draft file: one `drafter.complete(prompt.text,
  draft_message(text), Draft)`. The message is the abstract text only: not the title, not
  the id. The row is appended and flushed at once (`question_id` is `q01` to `q40` by draw
  position, `kind` `answerable`, `drafted_question` and `question` both the model's
  question, `keep` `yes`, `draft_model` the pinned id, `draft_prompt_version`
  `ask/draft-v1`). A `ReviewError` writes no row and counts as `failed`; the next run
  tries that abstract again. If the file already has `n` rows, nothing is drawn and
  nothing is called. Spend guard first: projected cost over the abstracts still to
  draft, `cost_usd(drafter.model, len(text) // 3 + 400, 150)` each, above `max_usd`
  (default `settings.max_run_usd`) raises `BudgetExceeded`. Returns `{"drawn",
  "already_drafted", "drafted", "failed", "cost_usd"}`.
- The owner edits the draft file by hand. To strike a question he sets `keep` to `no`. To
  rewrite one he edits `question` (the `drafted_question` column keeps the original). He
  adds five rows with `question_id` `u1` to `u5`, `kind` `unanswerable`, `source_doc_id`
  empty, `question` his text, `keep` `yes`.
- `finalize_questions` validates, then writes the final file. It refuses (`ValueError`
  with the line number or the counts) when: `keep` is anything but `yes` or `no`; a kept
  row has an empty `question`; a kept answerable row has an empty `source_doc_id` or a
  kept unanswerable row has one; `question_id` repeats; the kept counts are not exactly 25
  answerable and 5 unanswerable. The count message, exactly: `the draft keeps <a>
  answerable and <u> unanswerable questions; it must keep 25 and 5`. `origin` is
  `drafted` when `question == drafted_question`, `rewritten` when it differs, `owner` for
  unanswerable rows. It never overwrites: an existing final file is `FileExistsError`.
  Order: answerable by `question_id`, then unanswerable. Returns `{"answerable",
  "unanswerable", "rewritten", "struck"}`.

**CLI**: `elusion ask-draft --criterion criteria/auditable-decisions.md [--max-usd N]`
builds `AnthropicProvider(DRAFT_MODEL)`, runs `draft_questions` on the criterion's draft
path, prints the summary and the path. `elusion ask-questions --criterion
criteria/auditable-decisions.md` runs `finalize_questions` and prints the counts and the
path. Errors print `error: ...` and exit 1.

**Tests**: `test_the_draw_is_seeded_and_repeatable`, `test_a_different_seed_draws_other_ids`,
`test_the_draw_comes_only_from_the_index`, `test_the_draw_refuses_an_index_smaller_than_n`,
`test_the_drafter_gets_the_prompt_file_and_only_the_abstract_text` (the message equals
`draft_message(text)`; the source doc id and the title do not appear in system or message),
`test_the_draft_file_has_a_row_per_source_with_model_and_prompt_version`,
`test_a_rerun_sends_nothing`, `test_a_rerun_after_a_crash_drafts_only_the_missing`,
`test_a_review_error_leaves_no_row_and_is_retried`, `test_the_spend_guard_raises_before_any_call`,
`test_finalize_writes_25_and_5_with_origins`, `test_finalize_ignores_struck_rows`,
`test_finalize_refuses_wrong_counts_with_the_exact_message`,
`test_finalize_refuses_a_keep_value_that_is_not_yes_or_no`,
`test_finalize_refuses_an_unanswerable_row_with_a_source`,
`test_finalize_refuses_an_answerable_row_without_a_source`,
`test_finalize_refuses_repeated_ids`, `test_finalize_never_overwrites`,
`test_read_questions_round_trips`, and the two CLI commands.

**What must not happen**: the source doc id, a title, a gold label or any other abstract in
the drafting message; a draft call for a source already in the file; the final file
overwritten; either file written outside `data/gold/` or committed.

Builder's choices: (a) the seed is today's date as a number, `20261126`, like the other
seeds here. (b) The draw is from the index, so drafted sources are by construction not in
the labelled gold set. (c) Rows are appended as they arrive, so a crash after 23 calls
keeps 23 paid drafts.

**Hands to Task D2**: `read_questions`, `Question`, the final file, `DRAFT_MODEL`.

## Task D2: `ask-eval`

Create `src/elusion/ask/results.py` (two tables), `src/elusion/ask/metrics.py`,
`src/elusion/ask/tracking.py`, `src/elusion/ask/report.py`,
`src/elusion/flows/ask_eval.py`, `src/elusion/review/prompts/ask/judge-v1.md`,
`tests/test_ask_metrics.py`, `tests/test_ask_eval_flow.py`, `tests/test_ask_tracking.py`.
Change `src/elusion/cli.py` (command `ask-eval`, tests in `tests/test_cli.py`) and
`tests/conftest.py` (`index_engine` also runs `ask.results.ensure_schema`). Tests first.

**Prompt file `ask/judge-v1.md`**, exactly this text:

```
You check whether one quoted passage supports one claim.

You are given the claim after CLAIM: and the passage after QUOTE:. Use only the quote. Answer with these fields:

- supported: true if a careful reader would accept the claim on the strength of the quote alone, false if the quote does not say it, says something narrower or different, or only hints at it.
- rationale: one sentence saying why.
```

Builder's choice: the judge sees the claim and the quote only, one call per claim.
Reason: "does this quote support this claim" is the spec's question, a claim a quote cannot
carry on its own is not grounded, and a call is about 300 tokens.

**`results.py`**: two tables in their own `MetaData`, `ensure_schema(engine)`.

`ask_eval_results`:
- `question` Text, `arm` Text, `answer_model` Text, `prompt_version` Text: the primary
  key (the spec's key; the question text itself, so an edited question is a new question
  and is paid for)
- `question_id` Text not null, `kind` Text not null
- `ask_id` BigInteger not null (the `ask_log` row)
- `k` SmallInteger not null
- `source_hit` Boolean null (null for unanswerable questions; for answerable ones, the
  source id is among the retrieved ids of that ask)
- `evaluated_at` DateTime(timezone=True) not null, server default `now()`

`ask_judgements`:
- `ask_id` BigInteger, `claim_index` SmallInteger, `judge_model` Text,
  `judge_prompt_version` Text: the primary key
- `supported` Boolean null (null: the judge gave no usable answer; the claim then counts
  as not supported and is reported separately)
- `rationale` Text null, `input_tokens` Integer, `output_tokens` Integer,
  `cost_usd` Numeric(10, 6)
- `judged_at` DateTime(timezone=True) not null, server default `now()`

Functions: `result_keys(engine, answer_model, prompt_version) -> set[tuple[str, str]]`
(question, arm), `add_result(...)` (`ON CONFLICT DO NOTHING`, returns bool),
`judged(engine, judge_model, judge_prompt_version) -> set[tuple[int, int]]`,
`add_judgement(...)` (`ON CONFLICT DO NOTHING`).

**`flows/ask_eval.py`**: a Prefect flow.

```python
def judge_message(claim: str, quote: str) -> str    # f"CLAIM:\n{claim}\n\nQUOTE:\n{quote}"

def ask_eval(criterion: str, answerer: Completer, judge: Completer, embedder: Embedder, *,
             engine=None, questions_csv=None, reports_dir=None, max_usd=None,
             log_mlflow: bool = True) -> EvalResult
```

`EvalResult` is a frozen dataclass: `report_path`, `run_ids: dict[str, str]` (arm to run
id; empty without MLflow), `asked`, `judged`, `cost_usd`.

1. Read the final questions file (`read_questions`; missing file: `FileNotFoundError`
   naming `elusion ask-questions`). `ensure_schema` for every table.
2. Todo asks: every (question, arm in `ARMS`) not in `result_keys(answerer.model,
   "ask/v1")`.
3. Spend guard before any call. Projected cost: for each todo ask,
   `cost_usd(answerer.model, K * (avg_chars // 3 + 60) + 700, 600)` with `avg_chars` the
   mean text length in `abstract_index`, plus `cost_usd(judge.model, 300, 80) * 3` per
   todo ask of an answerable question (three claims assumed). Above `max_usd`:
   `BudgetExceeded`, naming both figures.
4. For each todo ask: `ask(engine, question.question, answerer, embedder, arm=arm)`
   (default `k`, so `K`). The source id is never passed. Then `add_result` with
   `source_hit` computed by comparing `question.source_doc_id` with the retrieved ids of
   the ask (answerable only). An `error` ask still gets its result row: retrieval
   happened, and the key is held.
5. Judging. For every result of this (answer model, `ask/v1`) whose ask has claims and
   whose claim check passed (retrieved and verbatim) and has no judgement under
   (`judge.model`, `ask/judge-v1`): one `judge.complete(judge_prompt.text,
   judge_message(claim, quote), Support)` (`Support` has `supported: bool` and
   `rationale: str`, `extra="forbid"`). A `ReviewError` stores a judgement with
   `supported` null. A claim whose check failed is not sent to the judge and counts as
   not supported.
6. Compute one `ArmReport` per arm (below) and write the report, then one MLflow run per
   arm when `log_mlflow`.

**`metrics.py`** (pure, no database):

```python
@dataclass(frozen=True)
class EvalRow:
    question_id: str
    kind: str                       # "answerable" or "unanswerable"
    source_hit: bool | None
    outcome: str                    # answered, refused, failed, error
    claims: tuple[ClaimResult, ...]  # empty when there are no claims

@dataclass(frozen=True)
class ClaimResult:
    checked: bool                   # retrieved and verbatim
    supported: bool | None          # judge's answer; None when not judged or unusable

@dataclass(frozen=True)
class ArmReport:
    arm: str
    k: int
    n_answerable: int
    n_unanswerable: int
    recall_at_k: Estimate
    claims_total: int
    claims_supported: int
    groundedness_claims: Estimate | None
    answered_questions: int
    fully_grounded: int
    groundedness_questions: Estimate | None
    refusals_correct: int
    refusals_wrong: int
    errors: int
    judge_unusable: int
    cost_usd: float

def arm_report(arm: str, k: int, rows: Sequence[EvalRow], cost_usd: float) -> ArmReport
```

Definitions, exact:
- `recall_at_k`: `evaluate.metrics.proportion(hits, n_answerable)`, hits being rows
  with `source_hit` true. Every answerable row counts, whatever the model said.
- A claim is supported when `checked` and `supported is True`.
- `groundedness_claims`: `proportion(claims_supported, claims_total)` over the claims of
  all 30 questions' answers; `None` when there are no claims.
- `answered_questions`: rows with at least one claim (outcome `answered` or `failed`).
  `fully_grounded`: of those, rows whose every claim is supported.
  `groundedness_questions`: `proportion(fully_grounded, answered_questions)`; `None` when
  none answered. This is the interval to trust, because claims inside one answer are not
  independent.
- `refusals_correct`: unanswerable rows with outcome `refused`, of `n_unanswerable`.
  `refusals_wrong`: answerable rows with outcome `refused`. `errors`: rows with outcome
  `error`. `judge_unusable`: claims that were checked and have `supported` None after a
  judge call (stored as null).
- Ties between arms are not resolved here.

**`report.py`**: `render(reports: Sequence[ArmReport], ...) -> str` and the path
`docs/reports/ask-eval-<criterion>-<answer_model>-<prompt version with / as ->.md`, for
example `ask-eval-auditable-decisions-claude-sonnet-5-5-ask-v1.md` (use the same
character-cleaning regex as `flows.evaluate.report_path`). Plain lists, no tables. It
holds: the date (`report_date`), the index size and the pair and threshold that built it,
k, the embedding model, answer model and prompt version, judge model and prompt version,
RRF constants, then per arm: recall@5 as `k of n`, the proportion and its Clopper-Pearson
95% interval, groundedness by claim and by question the same way, correct refusals as
`x of 5`, wrong refusals, errors, unusable judgements and cost. Then a per-question list
by `question_id` only: for each arm, hit or miss and the outcome. It carries no question
text, no source id and no abstract text. It ends with these five lines, verbatim:

- `recall@5 is a lower bound: another abstract may also answer the question.`
- `The questions were drafted from abstracts, so they share vocabulary with their source even after rewording; this flatters lexical search.`
- `Groundedness by claim treats the claims of one answer as independent, which they are not; the by-question figure is the one to trust.`
- `The answer model and the judge are different models, so no model grades itself.`
- `Only abstracts the screen flagged are searched; one the screen discarded cannot be found.`

**`tracking.py`**: `log_arm_run(report: ArmReport, params: dict[str, str], artifacts:
list[Path], tracking_uri: str) -> str`. `import mlflow` inside the function. Experiment
`elusion/ask`; run name `<arm>/<answer_model>/<prompt_version>`; tags `phase` `7` and
`elusion.arm` the arm. Params: `arm`, `k`, `embedding_model`, `answer_model`,
`prompt_version`, `judge_model`, `judge_prompt_version`, `rrf_c`, `fusion_depth`,
`ts_config` (`english`), `review_model`, `review_prompt_version`, `threshold`,
`index_size`, `questions_sha256` (hash of the questions file), `n_answerable`,
`n_unanswerable`, `report_path`. Metrics: each `Estimate` as
`<name>_point`, `_low`, `_high`, `_k`, `_n` for `recall_at_k`, `groundedness_claims` and
`groundedness_questions` (duplicate the five-line helper from `evaluate/tracking.py`, do
not import its private name), plus `refusals_correct`, `refusals_n`, `refusals_wrong`,
`errors`, `judge_unusable`, `cost_usd`. Artifacts: the report, `ask/v1.md`,
`ask/judge-v1.md`. Never logged: the questions file, `ask_log`, any gold file or label.
Like `evaluate`, every invocation logs new runs; the paid work is never repeated.

**CLI**: `elusion ask-eval --criterion criteria/auditable-decisions.md [--max-usd N]
[--no-mlflow]`. There is no `--k` and no `--arm`: k is `K` and all three arms always run.
It builds `AnthropicProvider(ANSWER_MODEL)`, `AnthropicProvider(JUDGE_MODEL)` and
`OpenAIEmbedder()`, prints the report path and one `mlflow run <arm> <id>` line per arm.
`BudgetExceeded`, `FileNotFoundError`, `ProviderConfigError` print `error: ...` and exit 1.

**Tests**:
- `test_ask_metrics.py`: `test_recall_is_hits_over_answerable_with_an_interval`,
  `test_a_claim_with_a_failed_check_is_not_supported`,
  `test_a_claim_the_judge_could_not_answer_is_not_supported_and_is_counted`,
  `test_groundedness_by_question_needs_every_claim_supported`,
  `test_refused_and_error_rows_are_not_in_the_by_question_denominator`,
  `test_correct_refusals_are_counted_over_the_unanswerable`,
  `test_wrong_refusals_are_counted_over_the_answerable`,
  `test_no_claims_gives_none_not_a_division_by_zero`.
- `test_ask_eval_flow.py` (`index_engine`, fakes, `prefect_test_harness`, a made-up
  questions file of 2 answerable and 1 unanswerable under `tmp_path`):
  `test_every_question_arm_pair_is_asked_once` (the second run makes no answerer, judge or
  embedder call), `test_the_judge_is_called_only_for_claims_that_passed_the_check`,
  `test_a_judged_claim_is_not_judged_again`,
  `test_an_unusable_judgement_is_stored_and_not_retried`,
  `test_a_new_answer_model_is_a_new_key`, `test_an_edited_question_is_a_new_question`,
  `test_no_source_doc_id_appears_in_any_message_to_any_model` (scan every recorded
  system and message of the answerer, judge and embedder for each source id),
  `test_source_hit_comes_from_the_retrieved_ids`, `test_an_error_ask_still_holds_its_key`,
  `test_the_spend_guard_raises_before_any_call`, `test_a_missing_questions_file_names_the_command`,
  `test_the_report_has_no_question_text_no_source_id_and_the_five_lines`,
  `test_one_mlflow_run_per_arm`.
- `test_ask_tracking.py`: in the style of `tests/test_tracking.py` (a file store under
  `tmp_path`): `test_params_and_metrics_are_logged`,
  `test_the_questions_file_is_never_an_artifact`.
- `test_cli.py`: `test_ask_eval_has_no_k_option`, `test_ask_eval_prints_the_report_and_runs`,
  `test_ask_eval_errors_exit_1`.

**What must not happen**: an ask or a judge call for a key already in the tables; the
source id, a title or any label in a prompt; question texts or source ids in a report, an
MLflow artifact or git; a changed k; a result written for a question not in the file;
a model grading its own output (the answer model and the judge are different, and a test
asserts `ANSWER_MODEL != JUDGE_MODEL`).

**Hands to Task E**: the report, the MLflow runs, the per-arm figures.

## Task E: the phase note and the docs

Change `docs/notes/phase-7.md` (new), `docs/PHASES.md`, `docs/DECISIONS.md`,
`docs/ARCHITECTURE.md`, `CLAUDE.md`, `README.md`. This task starts only after the paid
runs of "Needs the owner" are done; the numbers it states are copied from the
`ask-eval` report and the part A report, never invented. No code changes.

- `docs/notes/phase-7.md`: the four headings of `docs/notes/README.md`, exactly `What we
  built`, `Why we built it`, `What we learned`, `Results`, about half a page. The builder
  drafts the three it can (with a first line `<!-- Draft; the owner edits it. -->`, as the
  phase 6 note has) and leaves `What we learned` for the owner, with the one line
  `<!-- The owner writes this in his own words. -->`. `Results` gives the part A figures
  and the three arms' recall@5 and groundedness with their intervals and sample sizes, and
  says how far they can be trusted: the five report lines of Task D2, and that 30 questions
  give wide intervals. It also holds the short table that maps the repo's words to the
  systematic-review words (included, excluded, screening) and the e-discovery ones
  (responsive, null set), from call 14.
- `docs/PHASES.md`: tick the three Phase 7 boxes for `ask`, the gold questions and the
  note, and only those that are true.
- `docs/DECISIONS.md`, one dated line each: the value of k and why (written in Task B);
  the prices and ids confirmed, with the page and date; the correction that the index
  holds about 10,150 abstracts, not a few hundred; the exclusion of held-back ids; the
  winning arm and the rule that chose it (best recall@5 point estimate; a tie goes to
  `lexical`, the arm with no embedding call; builder's choice); the result of the owner's
  confirmations.
- `docs/ARCHITECTURE.md`: a new section "Phase 7 part B: the index, ask and ask-eval
  (built)" after the phase 6 one, saying in plain words how data flows from `scores` and
  Parquet into `abstract_index` and `abstract_vectors`, through `retrieve` and `ask` into
  `ask_log`, and through `ask-eval` into `ask_eval_results`, `ask_judgements` and MLflow;
  why each store exists; the idempotence key of each. Add the row to the table "Where each
  phase reads and writes" and a note under "Local services" that the `postgres` image is
  the pgvector build.
- `DEFAULT_ARM` in `src/elusion/ask/retrieve.py`: the one-line change to the winning arm
  is the only code edit in this task, made by the owner or on his say-so, in the same
  commit as its DECISIONS line.
- `CLAUDE.md`: in the Commands block add `index`, `ask`, `ask-draft`, `ask-questions` and
  `ask-eval` with the flags of Tasks A to D2 (paid ones prefixed `op run
  --env-file=.env.tpl --`); in Layout add `src/elusion/ask/` ("pgvector index, the three
  retrieval arms, `ask`, gold questions, `ask-eval`"); in Stack, Postgres is the pgvector
  build. Keep the hard rules as they are.
- `README.md`: the phase 7 lines at about line 79 to 89 say what part B added and where
  the note is. Plain words; no claim the numbers do not support.

## Needs the owner

Costs are rough, for about 10,150 abstracts of about 400 tokens each, and become firm
once the prices under "To confirm" are read. The project stands near $23 of its $40; part
B should add about $1 to $3 and every paid flow is guarded at `ELUSION_MAX_RUN_USD`.

Every command that calls a paid model needs Touch ID (`op run`), so the owner must be at
the Mac. Do steps 4 to 6 in one sitting.

1. Before Task A: confirm the `postgres` service runs the pgvector build of Postgres 16
   on the same volume, and that the `pg_dump` of the paid ledger exists. Free.
2. Before Task A's paid run: if the builder could not read the provider pages, read them
   (or paste the facts) for the four items under "To confirm". Free.
3. Before the first paid run: answer yes or no to the one change from the spec, that the
   index leaves out the ids held back for the gold set (item 2 of "Where the spec could not
   be followed as written"). A no means the index is by scores alone and Task A drops the
   `held` filter and keeps the rest. Free.
4. Run the index (Task A). It embeds about 4 million tokens, about $0.05 to $0.15.

================
Command to paste:
```
op run --env-file=.env.tpl -- uv run elusion index --criterion criteria/auditable-decisions.md --prompt-version v1 --review-model gpt-6-luna
```

================

   Pass: it prints `embedded` close to `screened_in` minus `held_back_removed` and a cost
   under $0.20, and running it again prints `embedded: 0` and spends nothing. Fail: any
   traceback, or a second run that spends money.
5. Smoke-test `ask` with one question on each arm, about $0.01 to $0.05 together. The
   first command is the hybrid arm; run it again with `--arm lexical` and then `--arm
   vector`.

================
Command to paste:
```
op run --env-file=.env.tpl -- uv run elusion ask "How is recall estimated when a model does the first-pass review?" --arm hybrid
```

================

   Pass: each prints a first line starting `ask `, then claims with a quote and `check:
   ok`, or the refusal text `The passages do not answer the question.` Fail: a traceback,
   or `check failed` on every claim.
6. Draft the questions (Task D1): 40 Opus calls, about $0.10 to $0.20.

================
Command to paste:
```
op run --env-file=.env.tpl -- uv run elusion ask-draft --criterion criteria/auditable-decisions.md
```

================

   Pass: it prints 40 drafted and the path of the draft file. Fail: a traceback.
7. By hand, no model, about 45 to 60 minutes: open
   `data/gold/auditable-decisions.questions-draft.csv`. Set `keep` to `no` on 15 of the 40
   rows (or rewrite the `question` of a row you want to keep but dislike), so exactly 25
   answerable rows stay `yes`. Add 5 rows with `question_id` `u1` to `u5`, `kind`
   `unanswerable`, an empty `source_doc_id`, your question in `question`, and `keep`
   `yes`: each one a question you are sure no screened-in abstract answers. Do not look
   at what the three arms retrieve for them.
8. Finalize the question file. Free, no key needed.

================
Command to paste:
```
uv run elusion ask-questions --criterion criteria/auditable-decisions.md
```

================

   Pass: it prints 25 answerable and 5 unanswerable and a path. Fail: an `error:` line
   naming the count or the row.
9. Run the evaluation (Task D2): 90 Sonnet asks and about 200 Opus judgements, about
   $0.5 to $2.5 depending on Sonnet's price.

================
Command to paste:
```
op run --env-file=.env.tpl -- uv run elusion ask-eval --criterion criteria/auditable-decisions.md
```

================

   Pass: it prints a report path and three `mlflow run` lines, and running it again
   prints the same figures and spends nothing. The runs are at http://localhost:5001 (the
   experiment is `elusion/ask`). Fail: a traceback, or a second run that spends money.
10. Pick the winning arm from the report, or tell the builder to apply the rule in Task
    E; either way `DEFAULT_ARM` changes in one line.
11. Write `What we learned` in `docs/notes/phase-7.md` in your own words.
12. Review and merge the part B pull request. Merging is the owner's step.
