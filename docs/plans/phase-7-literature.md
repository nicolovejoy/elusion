# Phase 7 spec: the literature matter

**The hold of 2026-11-17 was lifted the same day.** The owner settled what phase 7 does with
the abstracts: the concept board (`phase-7-concept-board-map.md`) is built after tasks 4 to
9 and does not replace them (`docs/DECISIONS.md`, 2026-11-17). The owner approved the
criterion's wording on 2026-11-18 (task 4). Task 5, the paid runs, is next and is the
owner's to start.

Status: drafted 2026-11-16; the owner confirmed all fifteen calls as recommended the same
day (`docs/DECISIONS.md`). Tasks 0 to 4 are done (results under each task); the owner approved
the criterion's wording on 2026-11-18, and task 5, the paid runs, is next.
Later on 2026-11-16 the owner widened the search to agent papers, moved the
window to 2025-01-01 and added a fifth anchor ("Changed by the owner" under calls 3
and 6); where an older line below says 2023 or nine terms, those blocks govern. On 2026-11-17
the owner made the first paid review a probe of 1,500 abstracts, not 200 (the same heading
under call 4). Read `docs/ARCHITECTURE.md`,
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

   **Changed by the owner, 2026-11-16: agent terms, and a window from 2025.** The owner
   asked how to reach other work on agents whose decisions can be audited. The nine
   terms find such a paper only when it happens to use one of the nine phrases. Two
   changes follow, and they replace the table above.

   *The window* is works published since 2025-01-01, not 2023-01-01. It removes little:
   most of this literature is from 2025 and 2026 ("audit trail" has 230 works in 2023,
   357 in 2024, 1,454 in 2025 and 4,708 so far in 2026).

   *The terms* are sixteen: the nine above, searched stemmed as before, and seven more,
   searched with the `.exact` form. Stemmed, "agentic" becomes "agent" (766,639 works)
   and "auditability" becomes "audit", so the new terms spell out their plurals and
   turn stemming off. The custody row records which form each term used. Counts on
   2026-11-16, since 2025-01-01, with an abstract, `corpus=core`:

   | Term | Form | Works |
   |---|---|---|
   | "technology-assisted review" | stemmed | 31 |
   | "high-recall retrieval" | stemmed | 19 |
   | "citation screening" | stemmed | 131 |
   | "e-discovery" OR "electronic discovery" | stemmed | 100 |
   | "LLM-as-a-judge" | stemmed | 2,228 |
   | "algorithmic accountability" | stemmed | 1,231 |
   | "explainable AI" AND audit | stemmed | 1,383 |
   | "automated decision-making" | stemmed | 2,803 |
   | "audit trail" | stemmed | 6,162 |
   | agent phrase AND (audit, audits, auditing, auditable, auditability) | exact | 4,142 |
   | agent phrase AND (accountability, accountable) | exact | 2,175 |
   | agent phrase AND (traceability, traceable, provenance) | exact | 2,409 |
   | agent phrase AND ("human oversight", "human-in-the-loop") | exact | 2,116 |
   | agent phrase AND (logging, observability, "audit log", "audit logs") | exact | 1,224 |
   | "AI audit", "AI audits", "AI auditing", "algorithm audit", "algorithm audits", "algorithmic audit", "algorithmic audits", "algorithmic auditing" | exact | 1,276 |
   | "decision provenance" | exact | 94 |

   An *agent phrase* is any of "AI agent", "LLM agent", "LLM-based agent", "agentic AI",
   "agentic system", "agentic workflow" and "autonomous agent", or the plural of one
   ("agentic AI" has none). The exact strings are in `src/elusion/collect/terms.py`.

   27,524 hits before duplicates are removed, and about 20,700 distinct works: the nine
   terms return 10,977 between them and the seven add about 9,750. The second figure is
   an estimate, because one request cannot mix the two forms; the harvest's own count
   replaces it. All of it is reviewed.

   Not taken: a fifth group, about 7,100 more works, of language-model papers that are
   not about agents (language models AND auditable or auditability; language models AND
   "human oversight"; contestability AND AI). It was the largest group and the least
   about agents.

4. **The reviewer.** gpt-6-luna, the champion's model, with a new `auditable-decisions/v1`
   prompt built on the structure of `cloud-concentration/v2`; judge `claude-opus-5-5`
   with its own `judge-v1`; grader as in phase 4. No leaderboard for this criterion.
   The first 200 abstracts are reviewed and read before the rest, and if the prompt
   needs a change it becomes v2 (a published prompt version is never edited).
   Recommended.

   **Changed by the owner, 2026-11-17: the probe is 1,500 abstracts, not 200.** A
   review reads the topic `abstracts.ingested` from its start, in the order the
   harvest published. The first 1,389 messages are the one-page run of 2026-11-16:
   the first page of each of the sixteen terms, in the order of `terms.py`. So the
   first 200 are all from the four narrow terms (31 for technology-assisted review,
   19 for high-recall retrieval, 100 for citation screening, 50 for e-discovery),
   where far more than the corpus's share will be flagged, and a prompt that flags
   too much on agent papers would not show. The first 1,500 hold a page from every
   term. They cost about $0.30 where 200 cost
   $0.04. They are OpenAlex's top-ranked hits for each term, so they are still richer
   than the corpus: the probe checks the prompt and does not estimate the flag rate.

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

   **Changed by the owner, 2026-11-16: a fifth anchor, and the window from 2025.** The
   new terms of call 3 are about agents and none of the four anchors is, so harvest
   recall would not have tested them. The fifth anchor is Chan and others 2024,
   "Visibility into AI Agents" (FAccT), OpenAlex `W4399364360`: a paper about
   identifying, monitoring and logging what agents do. It was chosen over the two large
   surveys of language-model agents (2,796 and 4,548 citing works in the 2023 window),
   because few works that cite a general survey are about auditing. The citing works
   are those published since 2025-01-01, the window of call 3, and harvest recall is
   the fraction of relevant seeds that the sixteen terms also returned. Citing works
   in that window, with an abstract, on 2026-11-16:

   | Anchor | OpenAlex id | Citing since 2025-01-01 | Seeds |
   |---|---|---|---|
   | Cormack and Grossman 2014 | `W2029075138` | 23 | all 23 |
   | O'Mara-Eves and others 2015 | `W4313371821` | 190 | 75 |
   | Raji and others 2020 | `W3001807593` | 1,757 | 75 |
   | Zheng and others 2023 | `W7133239970` | 10,632 | 75 |
   | Chan and others 2024 | `W4399364360` | 231 | 75 |

   323 seeds. The 44 and the 269 in the task 0 results below were for the 2023 window.

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
uv run elusion harvest
uv run elusion review --criterion criteria/auditable-decisions.md --provider openai --model gpt-6-luna --sample 1500    # the probe
uv run elusion review --criterion criteria/auditable-decisions.md --provider openai --model gpt-6-luna --sample 25000   # the rest: SAMPLE is an upper limit, and scored ids are skipped
uv run elusion label --criterion criteria/auditable-decisions.md --prompt-version v1 --year 2025 --year 2026 --review-model gpt-6-luna --flagged-n 400 --discard-n 400 --n 30 --exclude b5a579121f7c0f6a --exclude 4839dd2f05549e59 --exclude 11e01572681982b3 --exclude 7e84529379fbbf14 --exclude cb52fb2ee7bc1a71 --exclude c70aa5cfcdacaff1
uv run elusion judge --criterion criteria/auditable-decisions.md --prompt-version v1 --year 2025 --year 2026 --review-model gpt-6-luna --flagged-n 400 --discard-n 400 --only-labeled   # the owner's 30 first
uv run elusion judge --criterion criteria/auditable-decisions.md --prompt-version v1 --year 2025 --year 2026 --review-model gpt-6-luna --flagged-n 400 --discard-n 400 --seeds          # then the rest
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

   **Results (2026-11-16).** Read from the OpenAlex documentation, which now lives at
   https://help.openalex.org (the old `docs.openalex.org` addresses redirect there), and
   checked against the live API the same day, without a key. Each line says which.

   *Access.*

   - A key is needed. Without one the first page of a query works, and the request for
     the second page of a cursor-paged query returns `401 API key required for cursor
     paging` (checked). Every search term but two returns more than one page.
   - The key is free: an account at https://openalex.org/settings/api. It is passed as
     the query parameter `api_key` or as an `Authorization: Bearer` header (docs). The
     harvest uses the header, so the key is never part of a URL that could be logged.
   - The variable is `OPENALEX_API_KEY`, in `.env.example` (empty) and `.env.tpl`
     (`op://dev-secrets/OpenAlex elusion/credential`). `harvest` refuses to start
     without it. The owner creates the account; the builder never sends an address.
   - The "polite pool", where a request carried a contact address in `mailto`, is gone:
     `mailto` is ignored (docs, deprecations page). No address is asked for anywhere.

   *Cost and limits.* OpenAlex is no longer simply free. It is metered, with a free
   daily allowance.

   - A key has $1 of usage a day; no key has $0.10 a day, shared by everyone on the same
     IP address (docs, and the notice in every keyless response).
   - A search call costs $0.001, a filter-only call $0.0001, a single work by id nothing
     (docs; the `cost_usd` in each response agreed). A "call" is one page.
   - The harvest is about 165 search pages of 100 works, about $0.17, and four citation
     calls at $0.0001. It fits in one day's allowance with room to run it five times.
   - A new account spends its first dollar at no more than $0.10 every five minutes
     (docs), so the first harvest takes about ten minutes however fast the code is.
   - Over any limit the API returns `429` with a `Retry-After` header (docs). The
     harvest waits that long and retries. The limit is 30 requests a second with a key.
   - Every response carries `x-ratelimit-remaining-usd` and `x-ratelimit-reset`, the
     seconds until midnight UTC (checked).
   - Task 0 itself spent about $0.03 of the keyless allowance.

   *Paging.*

   - `per_page` is at most 100; 200 is deprecated (docs).
   - Cursor paging: the first request carries `cursor=*`, each response carries
     `meta.next_cursor`, and the end is a null `next_cursor` with empty `results` (docs;
     the first page checked).
   - Page-number paging stops at 10,000 results (docs), which "audit trail" (6,749)
     does not reach but a broader term would. The harvest uses the cursor throughout.

   *The search.*

   - The query is the parameter `search.title_and_abstract`. The filter form used for
     the counts in call 3, `title_and_abstract.search:`, still works but is deprecated
     (docs). The two return the same counts: 54, 144, 1,487 and 6,749 for the four terms
     checked.
   - Quoted phrases and upper-case `AND`, `OR`, `NOT` are supported (docs; both boolean
     terms checked).
   - The search is *stemmed* by default: words are reduced to their root, so "reviews"
     matches "review". The `.exact` form turns that off and returns 50 for
     "technology-assisted review" against 54. The harvest keeps the stemmed default,
     which is what the counts in call 3 were taken with.
   - Since August 2026 a query reads the *core* corpus unless told otherwise.
     `corpus=all` adds an "expansion" set of records from repositories and returns 64
     for the same term (checked). The harvest sends `corpus=core` explicitly, so a
     change of default cannot change the population, and the custody row records it.
   - The filters are `from_publication_date:2023-01-01` and `has_abstract:true`, joined
     by a comma, which means AND (docs; checked).

   *The fields.* Requested with `select`, all at the root of a work (checked on one):
   `id`, `doi`, `title`, `publication_year`, `publication_date`, `language`, `type`,
   `updated_date`, `abstract_inverted_index`.

   - `title` can be null (docs). A work with no title is stored with an empty one.
   - `language` is detected by OpenAlex, not declared by the publisher (docs).
   - `updated_date` moves when the citation count moves (docs), so it says nothing
     about whether the abstract changed. The content hash is what detects that.
   - Filtering by `updated_date` needs a paid plan (docs). The harvest does not need it.
   - One abstract was rebuilt from its inverted index with no gaps.
   - The data is CC0 (docs, pricing page). OpenAlex serves the inverted index and not
     the plain text "for legal reasons" (docs). The rebuilt text stays in `data/`, which
     is gitignored, as the anti-goals already require.

   *The anchors.* Resolved by title search; each title matched one work of the right
   year and venue. "Citing, in window" is the count of works that cite the anchor, were
   published since 2023-01-01 and have an abstract: the pool that call 6 samples from.

   | Anchor | OpenAlex id | Cited by | Citing, in window |
   |---|---|---|---|
   | Cormack and Grossman 2014 | `W2029075138` | 194 | 44 |
   | O'Mara-Eves and others 2015 | `W4313371821` | 883 | 354 |
   | Raji and others 2020 | `W3001807593` | 3,047 | 2,218 |
   | Zheng and others 2023 | `W7133239970` | 14,375 | 12,735 |

   - The citing works are found with the filter `cites:` and the anchor's id (checked;
     it is not on the documentation pages read).
   - The sample is `sample=75` with `seed`, in one page. The same seed returned the
     same 75 ids in the same order twice running; a different seed shared 3 of 75
     (checked on Raji). The documentation does not promise the same sample after the
     catalogue changes, so the custody rows are the record of what was drawn, and a
     re-run does not draw again for an anchor that already has its seeds.
   - Cormack and Grossman has 44 citing works in the window, fewer than the 75 of call
     6. Settled 2026-11-16: take all 44, which is a census of that pool and not a
     sample, and 75 from each of the others, 269 seeds in place of 300. E-discovery is
     a small research field, so another anchor for that strand would have a small pool
     too.
   - The O'Mara-Eves paper has an erratum (`W2147469877`) that is itself cited 354
     times. Works that cite only the erratum are outside the pool. Left as it is.
1. **Two corpora.** Call 7. Tests first: an `edgar` review never reads an `openalex` id
   and the reverse; the 2018-2019 report regenerates byte for byte.

   **Results (2026-11-16).** 290 tests pass, 15 of them new.

   - `src/elusion/corpora.py` is the one committed place: it defines the two corpora
     (name, topic, text directory) and maps each criterion to one. A criterion that is
     not in the map is refused before a job row or a model call exists.
   - `documents` has `corpus` (`edgar` by default) and `cik` is nullable.
     `collect.db.ensure_schema` alters an existing table; the laptop's 1,604,015 rows
     all read `edgar`. A dump taken first is in `data/backups/`.
   - `review`, `judge`, `grade` and `label` read the topic and the text directory of
     the criterion's corpus. A document of the other corpus has no text there, so the
     run fails before any call. The consumer group names did not change.
   - The report's count of documents in the population's years is of the criterion's
     corpus only.
   - Not done, and why: the readers of `scores` take no corpus. A score's prompt
     version begins with its criterion, and a criterion has one corpus, so a pair's
     scores cannot span two.
   - The six committed reports were regenerated with the code before and after this
     task, with their original dates: each pair is identical. Against the committed
     files, the 2020 report is identical and the five 2018-2019 reports are not, with
     or without this task: phase 6 changed the report's wording and cost lines after
     they were written, and they were renamed but never regenerated.
2. **Harvest.** A Prefect flow: for each term, page through OpenAlex, rebuild each
   abstract, write the custody rows, the Parquet text, the `documents` rows and the
   topic messages. Safe to re-run: a known (work, hash) adds a custody row for the new
   harvest and nothing else. Network calls are retried; a test rebuilds an abstract
   from a recorded response, and no test calls the network.

   **Results (2026-11-16).** `uv run elusion harvest`, run once with `--max-pages 1`
   and then in full. 336 tests pass.

   - The full harvest took 284 pages and nine minutes, with no error and no `429`, and
     used $0.28 of the day's free $1.
   - 27,524 hits, the sum of the sixteen counts in call 3 to the unit, and 23,246
     distinct works: 5,253 published in 2025 and 17,993 in 2026. No work was returned
     without an abstract or a year.
   - 23,246 is more than the 20,700 estimated in call 3. The estimate took the nine
     stemmed terms' union from one request that ORed them together, which returned
     10,977; the harvest found 13,774 distinct works for the same nine terms, whose
     counts sum to 14,088. So that request under-counted, and the nine terms overlap
     far less than it suggested. Why is not established. The same holds for the
     12,471 quoted for the 2023 window. The seven exact terms return 10,169 works, as
     estimated, and 697 works are found by both groups.
   - 20,080 works were found by one term, 2,323 by two, 843 by three or more.
   - The four stores agree: 23,246 rows in `documents` under `openalex`, the same ids
     in `data/abstracts/year=2025.parquet` and `year=2026.parquet` (20 MB), 23,246
     messages on `abstracts.ingested`, and every document has a custody row. The
     1,604,015 `edgar` rows are untouched.
   - `harvest_custody` holds 28,968 rows: 27,524 for the full harvest and 1,444 for
     the one-page run before it, which the full run found again as known documents.
     The PRISMA counts must count distinct works per term, not rows.
   - The command takes no `--criterion`: the terms belong to the corpus, and there is
     one harvest. The terms, the window and the anchors are in
     `src/elusion/collect/terms.py`.
   - A document id is 16 hex characters, a hash of the work id and the hash of the
     text, so every table and file keyed by `doc_id` takes an abstract unchanged. The
     custody table maps a document id to its work id.
   - The text is the title, a blank line and the abstract. The median is 226 words,
     against the "twice a risk-factor paragraph" the cost estimate assumed.
   - The "recorded response" in the tests is made up, in the shape the API returned,
     because a real one would put an abstract in git.
   - Seen in the data and left alone, since all of it is reviewed: 855 abstracts are
     not in English by OpenAlex's detection; 793 works are of type `dataset` and 635
     of type `software`, records from data repositories whose "abstract" is a
     description; 92 texts are under 30 words.
3. **Citation route.** Call 6: the seeded sample of citing works per anchor, through
   the same flow, marked `citation`.

   **Results (2026-11-16 built, 2026-11-17 drawn).** After the search terms,
   `harvest` asks OpenAlex for `sample=75` with seed 20261116 of the works that cite
   each anchor, and stores them with the route `citation`, the anchor's id as the
   query and the seed in the custody row. An anchor that has `citation` rows is not
   drawn again. `--seeds-only` skips the searches. Checked without a key: a sample of
   75 from the 23 works citing Cormack and Grossman returns all 23. A draw must not
   run at the same time as another harvest, since both rewrite the same Parquet files.

   The owner ran `op run --env-file=.env.tpl -- uv run elusion harvest --seeds-only`
   on 2026-11-17 (harvest `ce741cd0d1784f3188191f544a26639b`, five requests):

   | Anchor | Seeds drawn | Also returned by a search term |
   | --- | ---: | ---: |
   | Cormack and Grossman 2014 | 23 | 9 |
   | O'Mara-Eves and others 2015 | 75 | 1 |
   | Raji and others 2020 | 75 | 15 |
   | Zheng and others 2023 | 75 | 1 |
   | Chan and others 2024 | 75 | 21 |

   - 323 custody rows and 322 distinct works: one work cites two anchors.
   - 47 of the 322 were also returned by a search term, and 275 are new documents. The
     corpus is now 23,521 abstracts, and `documents`, the two Parquet files and the
     topic agree on that number.
   - 47 of 322 is not harvest recall. Most works that cite an anchor are not about
     auditing a decision system (a paper that cites the LLM-judge survey is usually
     one that used an LLM judge). Harvest recall counts only the seeds the judge
     calls relevant, so it waits for task 5.
4. **Criterion and prompts.** `criteria/auditable-decisions.md`, review `v1`, `judge-v1`,
   `grade-v1`. The owner approves the wording before any call is paid for.

   **Results (2026-11-16): drafted, not approved.** The four files exist. The criterion
   is call 2's statement with agents made explicit in the audit-trail shape
   (identifying, monitoring and logging the actions of AI agents), after the owner's
   widening of call 3. The review scale: 0 not about an automated decision system; 1
   builds or applies one and reports accuracy; 2 mentions auditing or oversight with
   no method of its own; 3 a method, mechanism or measurement as one part of the
   paper; 4 as its main contribution. Flagged is 3 and above, as for the 10-K
   criterion. Until the first paid call these files may be edited; after it, a change
   is a new version.

   **Approved by the owner, 2026-11-18,** with one edit of his own: the sentence that
   introduces the documents now reads "The documents relating to our auditable decisions
   are essentially paper abstracts." and no longer says each is read with its title.
5. **Owner: the paid runs.** Review 1,500 (call 4, changed 2026-11-17), read the flags
   together, then review the rest;
   judge; label (call 5); grade; evaluate. Each is guarded at `ELUSION_MAX_RUN_USD` and
   run one at a time.

   **2026-11-18: the probe was started and failed at its first call, at $0.00.** OpenAI
   answered 401, "Incorrect API key provided", for the key in the 1Password item
   `OpenAI elusion` (vault `dev-secrets`). Job `b4d6b947` is marked failed with no
   documents scored, there are no scores for `auditable-decisions`, and the criterion is
   not yet frozen as `v1`. Not known: why the key is rejected. The cluster's own key was
   due to be revoked after the Azure day (`infra/README.md`, step 14), so the laptop's key
   may have been deleted with it or in its place. The probe waits for a working key in
   that item; the command is the first `review` line under "Goal".

   **Later on 2026-11-18: the cause found and the key replaced.** The OpenAI keys page
   lists both keys as revoked: the cluster's (`elusion-aks`) and the laptop's (`OpenAI
   elusion`, created 2026-11-06). The laptop's key was revoked along with the cluster's
   after the Azure day, so the 401 was correct. The owner created a new key,
   `elusion_phase_7`, and put it in the same 1Password item, so `.env.tpl` does not
   change. A free call that lists models answered 200 with it.

   **The probe ran, 2026-11-18.** Job `5b44f6fe`: 1,500 pulled, 1,500 scored, none
   failed, $0.35 ($0.000231 an abstract; the flow projected $0.29 from its first
   documents). The criterion and the review prompt are now frozen as
   `auditable-decisions/v1`. Scores: 0, 183; 1, 178; 2, 308; 3, 237; 4, 594. Flagged (3
   and above): 831 of 1,500, 55%. This is not a richness figure: the 1,500 are the first
   ids on the topic, in harvest order, and not a random sample. At the same price the
   remaining 22,021 abstracts cost about $5.09. Next: the owner and Claude read the flags
   together before the rest is reviewed.

   **The flags read, 2026-11-19: `v1` stands.** Claude read 81 abstracts, sampled by
   search term and score, and judged about half the 4s and nearly all the 3s off the
   criterion. The owner then judged six of them, keep or discard, and agreed with the
   model on four. Kept: `b5a579121f7c0f6a` (texanshootR, 4), `4839dd2f05549e59` (a
   proposed accountability framework, 4), `11e01572681982b3` (PROVISO, a controller that
   vets an agent's actions, 4), `7e84529379fbbf14` (the Cognitive Control Gap, 3).
   Discarded: `cb52fb2ee7bc1a71` (a systematic review of cancer-prognosis models, 4),
   `c70aa5cfcdacaff1` (a fraud detector with explanations, 3). His rule as applied: a
   paper about the topic is kept, whether a tool, a proposal or an argument, and kept
   when unsure; a paper mainly about something else is discarded. Claude's reading was
   narrower than his, and both changes Claude proposed for a `v2` (proposals with nothing
   built score 2; controls with no reviewer and no record score 2) would have discarded
   papers he wants. The score does not separate his keeps from his discards, so flagging
   at 4 only is not a fix. The six were picked as likely mistakes and are not a
   measurement. They are excluded from the owner's blind labels, since he has seen their
   scores. By type, datasets are flagged at 61% and software at 72% (19 of 31, 26 of 36).
   Not yet decided: the judge reads every flagged abstract in this plan, priced for
   about 700; with 831 flagged in the probe alone it must read a random sample of the
   flagged pile, and `split_piles` in `evaluate/gold.py` takes every flagged id today.
   Next: the full review under `v1`. The probe ran at 104 abstracts a minute with 8
   requests in flight, so the rest takes about 3.5 hours, or about half that with
   `--concurrency 16` if the key's rate limit allows.

   **The full review started, 2026-11-20.** Job `259b3152`, by the owner, with
   `--concurrency 16`.

   **The judge pass, 2026-11-20: a sample of each pile, and the judge checked against the
   owner first.** The owner chose the order (C below) and approved the whole of this
   block, sizes and the rule of step 5 included, on 2026-11-20.
   The implementation plan is `docs/superpowers/plans/2026-11-20-phase-7-judge-sample.md`,
   approved the same day: six tasks, test-first.

   *Result, 2026-11-20: built, not yet run.* `split_piles` takes `flagged_n` and draws
   the flagged sample with its own generator, so the discard draw (and the
   `cloud-concentration` gold set) is unchanged. `judge` has `--flagged-n`,
   `--only-labeled` and `--seeds`; `label` given `--year` picks from the draw before the
   judge has run, and takes `--exclude`; `grade` and `evaluate` take `--flagged-n` and
   read the same sample. The report prints the flagged-sample line and precision by
   score; recall and richness still use the sizes of the whole piles. Nothing stores the
   draw: every command recomputes it from the scores and the seed, so all of them must be
   given the same `--flagged-n`, `--discard-n` and seed. No paid call was made. Steps 3
   to 7 above are the owner's to start, once the review job has finished.

   *Why it changes.* The judge reads every flagged abstract plus 200 discards, priced for
   about 900 abstracts at 3% flagged. The probe flagged 55%. At that rate the flagged
   pile is near 10,000 and judging all of it costs $40 or more. `grade` has the same
   shape: it grades every flagged rationale.

   *Whose standard.* `judge-v1` has not run. It excludes "commentary with no method and no
   measurement" and a paper that "merely mentions" oversight, and it has no rule for an
   abstract the reader is unsure of. The owner's rule of 2026-11-19 keeps tools, proposals
   and arguments, and keeps when unsure. Not tested: this is read off the prompt, and the
   judge has labelled nothing yet. Three ways were put to the owner:

   - A. The judge as written. The gold set is the approved criterion text; the owner's
     labels show where he and the judge differ.
   - B. The judge rewritten to the owner's rule before it runs. The gold set matches him,
     and the judge and the frozen criterion text then differ.
   - C. Measure first, then choose A or B. **Chosen by the owner, 2026-11-20.**

   *The order under C.*

   1. The review finishes. The piles are drawn only then: the draw depends on the whole
      pile, and `judge` already refuses to run while a review job is unfinished.
   2. The gold ids are drawn with the seed: 400 of the flagged pile and 400 of the discard
      pile, both at random. `split_piles` gains a limit on the flagged pile.
   3. The owner labels 30 of them blind, 15 from each pile (40 until 2026-11-20, lowered at his request), with `elusion label`. Today
      `label` picks from abstracts the judge has already labelled; it must pick from the
      drawn gold ids instead. The six abstracts he judged on 2026-11-19 are left out. This
      replaces the 20 labels of call 5.
   4. The judge as written labels those 30 only, about $0.15. `judge` gains a way to label
      only the ids the owner has labelled.
   5. Agreement is counted, with its interval, and every disagreement is read with the
      owner. Claude's call, approved by the owner: at 27 or more of 30 the judge stands
      (A). Below that, B is taken if the disagreements are mostly abstracts he kept as
      proposals, arguments or doubtful cases and the judge refused.
   6. Under A, the judge labels the other 770. Its 30 labels are not paid for twice, and
      the owner's 30 are the agreement figure in the report.
   7. Under B, the judge prompt becomes `judge-v2`, written from the owner's rule in
      words. No abstract he labelled goes into it (the gold set is never in a prompt).
      `judge-v2` labels all 800. The owner labels a fresh 30 for the agreement figure in
      the report, because the prompt was changed after reading the first 30; the first 30
      are reported as the set the change was made on.
   8. What happened, 2026-11-21 (each step is a line in `docs/DECISIONS.md`): `judge-v1`
      matched the owner on 20 of 30. The owner's purpose is concepts and mental models,
      so B was taken: `judge-v2` from his rule, tested on the same 30 first (his call,
      an optimistic figure) and also 20; `judge-v3` without "if unsure, keep", 21. He
      re-read six labels and changed three, kept as revisions beside the blind ones: 23,
      23 and 22. The bar was given up and `judge-v2` labels the gold set. A fresh 30
      labeled later, blind, give the figure that was not fitted.

   *Also approved by the owner, as proposed by Claude.*

   - 400 from each pile gives precision to within about 5 points either way. The discard
     sample grows from 200 to 400 because about half the corpus is discarded, so the
     elusion sample moves the recall interval as much as the flagged one does.
   - Every flagged citation seed is judged as well, for harvest recall only. Precision is
     computed from the random 400 alone; adding the seeds would bias it.
   - `grade` grades the same 400 flagged abstracts, not the whole flagged pile.
   - The report gives precision for score 3 and score 4 separately, from the same sample.
     It is a description, not a fifth metric, and shows whether flagging at 4 only would
     help.
   - Cost: 800 abstracts and the flagged seeds, about 950 calls. Phase 6 paid $0.0026 a
     paragraph; if an abstract costs twice that, about $5. The judge's own first calls
     project the real figure against `ELUSION_MAX_RUN_USD` before the rest is spent.

   Not affected: the index of task 7 takes every abstract the review flagged and nothing
   from the judge. "Works kept" in the report is then a list of thousands; how it is shown
   belongs to task 6.
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

- review: 23,521 abstracts (the 23,246 harvested and the 275 seeds no term returned)
  at about $0.0002 = about $4.70, the probe's $0.30 included
- judge: every flagged abstract plus 200 discards. Phase 6 paid $0.70 for 266
  paragraphs; at 3% flagged this is about 900 abstracts of twice the length, about
  $4.75
- grade: about $0.50
- embeddings: cents
- question drafting, 30 questions across three arms, groundedness judging: about $1
- OpenAlex: $0.28 of metered usage a harvest (284 search pages), inside the free $1 a day that a key
  carries, so nothing is paid (task 0 results)

About $11.50, where the calls of 2026-11-16 said $8 before the agent terms were added;
the project would stand near $23 of its $40. Each run is guarded at `ELUSION_MAX_RUN_USD`. The judge figure is the soft one: it
grows with the flagged pile, and the 1,500-abstract probe in task 5 is where a prompt that
flags too much gets caught while it is still cheap.
