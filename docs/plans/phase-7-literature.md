# Phase 7 spec: the literature matter

Status: drafted 2026-11-16; the owner confirmed all fifteen calls as recommended the same
day (`docs/DECISIONS.md`). Nothing is built. Next is task 0. Read `docs/ARCHITECTURE.md`,
the Phase 7 checklist in `docs/PHASES.md`, `docs/FIELD-NOTES.md` (sections 2, 4 and 5)
and the 2026-11-15 phase 7 line in `docs/DECISIONS.md` first. Hard rules in `CLAUDE.md`
apply to every line below. The phase 6 spec (`docs/plans/phase-6-production.md`) is the
pattern this one follows. Phase 7 runs on the laptop only, from `compose.yaml`.

## Intent

Phases 1 to 6 reviewed one corpus, 10-K risk-factor paragraphs, against one criterion.
Phase 7 points the same machinery at a second corpus about the project's own subject:
recent research on automated systems making decisions that must be auditable. It then
adds the one thing the project has not done: answering a question from the documents the
review kept, with every step logged.

The phase has two parts, and the second starts only when the first has a report.

- **Part A, the screen.** Harvest abstracts broadly, review each against a written
  criterion, judge, evaluate, report. This is a systematic-review screen, and it is the
  same job as the 10-K sweep with new nouns.
- **Part B, the question.** Build a search over the screened-in abstracts and an `ask`
  command whose answers are made only of quoted passages. Measure how often the search
  finds the right abstract and how often an answer's claims are supported by its quotes.

The terms, once:

- **OpenAlex** is a free, open catalogue of scholarly works (papers, preprints, books)
  with an HTTP API. Each work has a permanent id such as `W2741809807`. Its data is
  released CC0, which is why abstracts may be stored here when news articles may not.
- An **abstract** arrives from OpenAlex as an *inverted index*: a map from each word to
  the positions where it occurs. The collector rebuilds the text from it. That rebuilt
  text is what gets hashed and reviewed.
- A **systematic review** is the research world's defensible review: a written question,
  a recorded search, every record screened against written criteria, and counts reported
  at each step. **Screening** is its word for first-pass review; a record is *included*
  or *excluded* where this repo says flagged or discarded.
- **PRISMA** (Preferred Reporting Items for Systematic reviews and Meta-Analyses) is the
  reporting standard for such reviews. Its flow diagram is a count at each stage: records
  identified by each route, duplicates removed, records screened, excluded, included.
- **Citation chasing** (also *snowballing*) finds papers by following citations from a
  paper already known to be on topic, instead of by search terms. It is a second route
  into the literature that does not depend on the wording of any query.
- **Harvest recall** is the fraction of on-topic papers that the search terms returned
  at all. Elusion cannot see it: a paper the terms never returned is not in the discard
  pile (FIELD-NOTES, section 5, caution 4).
- **Full-text search** in Postgres matches words: a `tsvector` is a document reduced to
  its normalised words, and a query ranks documents by how well those words match. This
  is *lexical* retrieval. It finds exact terms and misses paraphrase.
- An **embedding** is a list of numbers (a vector) that a model produces for a text, so
  that texts with similar meaning get nearby vectors. **Vector search** ranks documents
  by the *cosine similarity* between the question's vector and each document's. It finds
  paraphrase and can miss an exact name.
- **Hybrid retrieval** runs both and merges the two ranked lists. **Reciprocal rank
  fusion** is the merge used here: a document's score is the sum over the lists of
  `1 / (60 + its rank in that list)`, so a document near the top of either list ranks
  well and no score from one list has to be compared with a score from the other.
- **Retrieval-augmented generation** (RAG) is the pattern of `ask`: retrieve passages
  first, then have a model answer from those passages only.
- **recall@k** is the fraction of questions for which a known right document appears in
  the top k retrieved. **Groundedness** is the fraction of an answer's claims that the
  quoted passage actually supports.

Four facts shape the design:

- The review flow reads one Kafka topic, `documents.ingested`, from the beginning for
  each new (criterion, model, prompt version). A new criterion started today would walk
  1.6 million 10-K paragraphs and pay to screen them against the wrong question. The
  two corpora must be kept apart before any review of the new one can run (task 1).
- `documents` has `cik` and `company`, and populations are named by filing year. An
  abstract has neither a filer nor a filing year. It does have a publication year, so
  `--year` keeps its meaning.
- The screened-in set that `ask` searches must be defined by the reviewer's scores
  alone. The judge's labels are the gold set, and filtering the search by them would put
  gold into a retrieval context, which the hard rules forbid.
- Title-and-abstract search returns far fewer works than the full-text counts in
  DECISIONS (2026-11-15). Counts read from the OpenAlex API on 2026-11-16, for works
  published since 2023-01-01 that have an abstract, are in call 3.

## Calls for the owner (answered 2026-11-16)

Answered 2026-11-16: all fifteen as recommended. The wording of the criterion (call 2)
is still approved by the owner in task 4, before any call is paid for.

1. **Two parts, two pull requests.** Part A ends with the screening report and a pull
   request; part B is built after it merges. One phase note at the end covers both.
   Recommended, because part B's design depends on how many abstracts the screen keeps,
   which is not known until part A has run.

2. **The criterion.** Name `auditable-decisions`, file `criteria/auditable-decisions.md`.
   Proposed one-line statement, yours to reword:

   > Studies how the decisions of an automated system (a classifier, a language model or
   > an agent) can be checked or defended afterwards: measuring what it got right and
   > what it missed, recording what it did and why, or having a person review or
   > override it.

   Proposed to count: validation of automated document review or citation screening
   (recall, elusion, sampling, stopping rules); audit trails, logging, provenance or
   lineage for a model's or agent's decisions; studies comparing an LLM judge with human
   judges; procedures for human oversight of automated decisions. Proposed not to count:
   work that applies a model and reports only its accuracy; audit logs of systems that
   make no automated decision (financial ledgers, clinical audit); explanation methods
   presented with no audit or accountability use; legal or policy commentary with no
   method and no measurement. The last exclusion is the one most likely to be wrong for
   your purposes, since it removes most of the "automated decision-making" law
   literature.

3. **The harvest.** Nine search terms against title and abstract, works published since
   2023-01-01 with an abstract. Counts on 2026-11-16:

   | Term | Works |
   |---|---|
   | "technology-assisted review" | 54 |
   | "high-recall retrieval" | 24 |
   | "citation screening" | 171 |
   | "e-discovery" OR "electronic discovery" | 144 |
   | "LLM-as-a-judge" | 2,310 |
   | "algorithmic accountability" | 1,339 |
   | "explainable AI" AND audit | 1,487 |
   | "automated decision-making" | 3,756 |
   | "audit trail" | 6,749 |

   About 16,000 hits before duplicates are removed, close to the 20,000 of the earlier
   samples, and all of it is reviewed, so the population is the whole harvest and not a
   first-N slice. The first four terms are the narrow core; the other five are the broad
   net that keeps richness low. The last three terms are new since DECISIONS 2026-11-15;
   "predictive coding" stays out (it is also a neuroscience theory). Recommended as
   listed. Dropping "audit trail" roughly halves the cost and the noise.

4. **The reviewer.** gpt-6-luna, the champion's model, with a new `auditable-decisions/v1`
   prompt built on the structure of `cloud-concentration/v2`; judge `claude-opus-5-5`
   with its own `judge-v1`; grader as in phase 4. No leaderboard for this criterion.
   The first 200 abstracts are reviewed and read before the rest, and if the prompt
   needs a change it becomes v2 (a published prompt version is never edited).
   Recommended.

5. **Your labels.** `elusion label` on 20 abstracts (10 flagged, 10 discarded), blind,
   as in phase 3, so the report can state judge-against-owner agreement for this
   criterion. About 20 minutes of your time. Recommended.

6. **Harvest recall by citation chasing.** Pick four well-known anchor papers, one per
   strand of the topic. Fetch a seeded sample of 75 works per anchor from those that
   cite it, published since 2023-01-01, and add them to the corpus marked with the route
   `citation` and the anchor's id. They are reviewed and judged like everything else.
   The seeds the judge calls relevant are the "known on-topic papers", found without any
   search term. Harvest recall is the fraction of them that the nine terms also
   returned, with a Clopper-Pearson interval. Proposed anchors, to be resolved to
   OpenAlex ids in task 0: Cormack and Grossman 2014 (machine-learning protocols for
   technology-assisted review); O'Mara-Eves and others 2015 (text mining for study
   identification in systematic reviews); Raji and others 2020 (an end-to-end framework
   for internal algorithmic auditing); Zheng and others 2023 (judging LLM-as-a-judge).
   Recommended over a hand-made list, which would cost you an afternoon and be found
   mostly by the same words the terms use. Its limit, to be stated in the report: a seed
   the screen wrongly discards is never judged, so it is missing from the denominator.

7. **Keeping the corpora apart.** `documents` gains a `corpus` column (`edgar` for every
   existing row, `openalex` for the new ones); each corpus has its own topic
   (`documents.ingested` stays; `abstracts.ingested` is new) and its own text directory;
   a criterion names its corpus in one committed place, so there is no flag to forget and
   a criterion cannot be run against the other corpus. `cik` and `company` become
   nullable for the new corpus. Every existing command for `cloud-concentration` behaves
   as before, and the committed 2018-2019 report regenerates unchanged. Recommended.

8. **The chain-of-custody record.** A new table, one row per (work, query that returned
   it): OpenAlex work id, the query or the anchor, the route (`search` or `citation`),
   the harvest timestamp in UTC, and the SHA-256 of the rebuilt title and abstract. The
   document id is derived from the work id and that hash, so if OpenAlex later serves a
   different abstract for the same work, the re-harvest creates a new document and the
   reviewed one stays as it was. The PRISMA counts are queries over this table.
   Recommended.

9. **What `ask` searches with.** Three arms over the screened-in abstracts, compared on
   recall@k in MLflow: lexical (Postgres full-text search), vector (embeddings), and
   hybrid (the two merged by reciprocal rank fusion). `ask` uses the arm that wins.
   Recommended over lexical alone, because the comparison is the lesson: it shows on
   your own questions what each kind of search finds and misses. One whole abstract is
   one passage; nothing is split into chunks, because an abstract is already a
   paragraph.

10. **Where the vectors live.** Option A: in Postgres with the `pgvector` extension,
    which means changing the compose image from `postgres:16.15` to the pgvector build
    of Postgres 16 (same data directory, taken after a `pg_dump` of the paid ledger),
    and searching by exact scan with no index. Option B: in a Parquet file, compared in
    numpy, with no change to any service. At a few hundred abstracts both are exact and
    instant. A is recommended: lexical and vector search then sit in one table and the
    hybrid is one SQL statement, and pgvector is the smallest real vector store. It is a
    new image, so it is your call under "ask before adding scope".

11. **The embedding model.** OpenAI `text-embedding-3-small`, pinned by id and stored
    with every vector, through the SDK already installed and the key already in
    1Password; cost a few cents. The alternative is a local model through Ollama, free
    and pinned by digest. The builder confirms the id and its price from OpenAI's
    documentation before use. OpenAI recommended, for one less moving part.

12. **The answer model and the groundedness judge.** `claude-sonnet-5-5` writes the
    answers; `claude-opus-5-5` judges whether each quote supports its claim. They differ
    so that no model grades itself, as in phase 4. Prices are read from Anthropic's
    documentation and recorded in DECISIONS before the first call. Recommended.

13. **The gold questions.** 30 questions: 25 whose answer is in a known screened-in
    abstract, and 5 that the corpus cannot answer, where the right output is "not found
    in the screened-in set". Opus drafts one question from each of 40 abstracts drawn
    with a seeded RNG, told not to reuse the abstract's distinctive wording; you strike
    or rewrite, and keep 25. You write the 5 unanswerable ones. The file lives in
    `data/gold/` and holds, per question, the source document id, which never enters a
    prompt or a retrieval context. Recommended over 30 written by hand (about two
    hours). Its known bias, to be stated in the report: a question drafted from an
    abstract shares vocabulary with it even after rewording, which flatters lexical
    search.

14. **Vocabulary.** The repo keeps "relevant", "flagged" and "discard pile" (FIELD-NOTES
    cautions 1 and 2 stay undecided). The phase 7 report adds a short table mapping them
    to the systematic-review words (included, excluded, screening) and to the
    e-discovery ones (responsive, null set). No rename. Recommended.

15. **Cost.** About $8 of model spend (see "Expected cost"), against the "few dollars"
    in `docs/PROJECT.md`. Spend to date is about $12.30 on models plus the Azure bill,
    so the project would stand near $22 of its $40. Recommended; if you would rather hold
    it to $5, call 3 without "audit trail" does that.

## Goal

```
uv run elusion harvest --criterion criteria/auditable-decisions.md
uv run elusion review --criterion criteria/auditable-decisions.md --provider openai --model gpt-6-luna --sample 20000
uv run elusion judge --criterion criteria/auditable-decisions.md --prompt-version v1 --year 2023 --year 2024 --year 2025 --year 2026 --review-model gpt-6-luna
uv run elusion evaluate ...                      # the same flags; report gains the PRISMA counts and harvest recall
uv run elusion index --criterion criteria/auditable-decisions.md --prompt-version v1 --review-model gpt-6-luna
uv run elusion ask "How is recall estimated when a model does the first-pass review?"
uv run elusion ask-eval                          # recall@k per arm, groundedness; one MLflow run per arm
```

Command and flag names are the builder's; the shape is what matters.

## Tasks, in order

Part A.

0. **Facts before design is frozen.** The owner answers the calls. The builder reads the
   current OpenAlex API documentation for authentication, rate limits, cursor paging and
   the fields needed, resolves the four anchors to OpenAlex ids with their citing-work
   counts, and records all of it in this spec. If OpenAlex needs a key or asks for a
   contact address, it is an environment variable with a line in `.env.example` and an
   `op://` reference in `.env.tpl`, unset by default, never committed.
1. **Two corpora.** Call 7. Tests first: an `edgar` review never reads an `openalex` id
   and the reverse; the 2018-2019 report regenerates byte for byte.
2. **Harvest.** A Prefect flow: for each term, page through OpenAlex, rebuild each
   abstract, write the custody rows, the Parquet text, the `documents` rows and the
   topic messages. Safe to re-run: a known (work, hash) adds a custody row for the new
   harvest and nothing else. Network calls are retried; a test rebuilds an abstract
   from a recorded response, and no test calls the network.
3. **Citation route.** Call 6: the seeded sample of citing works per anchor, through
   the same flow, marked `citation`.
4. **Criterion and prompts.** `criteria/auditable-decisions.md`, review `v1`, `judge-v1`,
   `grade-v1`. The owner approves the wording before any call is paid for.
5. **Owner: the paid runs.** Review 200, read the flags together, then review the rest;
   judge; label (call 5); grade; evaluate. Each is guarded at `ELUSION_MAX_RUN_USD` and
   run one at a time.
6. **Report.** `evaluate` adds, for an `openalex` population: the PRISMA counts
   (identified per term and per route, duplicates removed, screened, excluded,
   included), harvest recall with its interval and its stated limit, and the vocabulary
   table of call 14. "Companies found" becomes "works kept", listed by OpenAlex id and
   title. The 10-K reports do not change. Pull request for part A.

Part B.

7. **Index.** Call 10: the screened-in abstracts (score at or above the threshold under
   the pair, nothing from the judge) with their `tsvector` and their embedding, each
   vector stored with the embedding model id. Re-running embeds only what has no vector
   for that model.
8. **`ask`.** Retrieve the top k by the chosen arm, then one call to the answer model
   with a versioned prompt. The answer is structured: a list of claims, each with a
   document id and a quote, or the statement that the passages do not answer the
   question. Python, not the model, checks that every quote is verbatim in the cited
   abstract and that the abstract was among those retrieved; an answer that fails is
   reported as failed, not repaired. Every ask writes one row to an `ask_log` table: the
   question as typed, the arm and k, the retrieved ids with ranks and scores, the model
   id the API returned, the prompt version, the answer, the check results, tokens and
   cost. That table is the audit trail of FIELD-NOTES section 2, steps 1, 3 and 4.
9. **Gold questions and `ask-eval`.** Call 13. recall@k is a hit rate over the 25
   answerable questions, one known source each, so it is a plain proportion and
   Clopper-Pearson applies; it is a lower bound, because another abstract may also
   answer the question. Groundedness is reported two ways: supported claims over all
   claims, and questions whose every claim is supported over questions, the second
   being the one whose interval can be trusted, since claims inside one answer are not
   independent. The 5 unanswerable questions are reported as a count of correct
   refusals. An eval result is keyed by (question, arm, answer model, prompt version)
   and never paid for twice. One MLflow run per arm.
10. **Owner: phase note, ticks, decisions, docs.** `docs/notes/phase-7.md` in the four
    headings; the Phase 7 boxes; a DECISIONS line per confirmed call; a phase 7 section
    in `docs/ARCHITECTURE.md`; the `CLAUDE.md` commands block and layout; `README.md`.

## Non-goals and anti-goals

- No agent: `ask` retrieves once and answers once. It does not plan searches, rewrite
  the question or loop. That is step 2 of the FIELD-NOTES audit trail and stays out.
- No reranking model, no chunking, no full texts or PDFs, no source but OpenAlex.
- No ANN index, no vector database beyond call 10, no UI, no service that stays up.
- Nothing on AKS, nothing of phase 5, no change to a 10-K score or report.
- Anti-goal: a gold label, a judge label or a gold question's source id in any prompt,
  in the index, or in what `ask` retrieves.
- Anti-goal: an abstract screened twice for the same (model, prompt version), or an
  eval question answered twice for the same key.
- Anti-goal: an email address or a key in git, in a URL in a committed file, or in the
  conversation.
- Anti-goal: abstract texts committed to git. Reports quote single sentences and list
  works by id and title.

## Left to the builder

- Command names, table and column names, the Parquet schema for abstracts, where a
  criterion states its corpus.
- Whether the OpenAlex client uses `httpx` (already installed with the Anthropic SDK,
  and then declared in `pyproject.toml`) or the standard library. No other new package
  without asking.
- The value of k, chosen before the gold questions are looked at and recorded.
- How title and abstract are joined into one document text.
- The language of the Postgres text-search configuration.

## Expected cost

Model spend, from the local ledgers: gpt-6-luna has cost $4.15 for 37,936 paragraphs,
$0.00011 each. An abstract with its title is about twice a risk-factor paragraph.

- review: about 16,300 abstracts at about $0.0002 = about $3.30
- judge: every flagged abstract plus 200 discards. Phase 6 paid $0.70 for 266
  paragraphs; at 3% flagged this is about 700 abstracts of twice the length, about $3.50
- grade: about $0.50
- embeddings: cents
- question drafting, 30 questions across three arms, groundedness judging: about $1
- OpenAlex: free

About $8, each run guarded at `ELUSION_MAX_RUN_USD`. The judge figure is the soft one: it
grows with the flagged pile, and the 200-abstract probe in task 5 is where a prompt that
flags too much gets caught while it is still cheap.
