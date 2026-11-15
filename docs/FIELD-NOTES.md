# Field notes: AI document review, its vocabulary and public framing

Compiled 2026-11-15 from public sources: Relativity's help documentation, blog and job
postings, one arXiv paper and trade press. Nothing here was re-verified in this
repository. Anything labeled "posting" comes from a job description and describes intent,
not shipped architecture.

These notes are background for the design work in this repo, phase 7 in particular. They
decide nothing; decisions are in `DECISIONS.md`.

## 1. Relativity's stated platform vocabulary

**The Relativity Intelligence Model** (posting 26-0360, Director of Product Management,
Data Intelligence Foundation; the only public source for this vocabulary). Its base is
the "Foundational Layer," a shared data platform serving all AI applications, made of
five primitives. The posting lists them as peers. The reading used in this project stacks
them:

1. **Query Plane** (top): unified retrieval. Standardized ingestion APIs independent of
   data source, hybrid retrieval (lexical + vector) with reranking, chunking as a managed
   capability, tiered storage (cold / warm / hot).
2. **Knowledge / Metadata** (the wide middle layer): "a unified materialized document
   layer, consistent across all workspaces." One row per document, the same shape in
   every workspace, kept current, serving both the review UI and AI teams.
3. **Files / Natives** (below): immutable storage for documents, images and native files,
   with SLAs and access contracts. Rows point to originals; nothing above copies them.
4. **Ontology / Relationship** (below): the semantic layer. What kinds of things exist in
   legal data and how they relate (Person, Communication, role CFO, attached-to), so
   Skills and Agents share a vocabulary and can reason rather than only filter.
5. **Data Capabilities** (below): reporting, audit, internal data infrastructure.
   "Observable, auditable, and explainable," called non-negotiable.

**One request traced through the planes.** Request: "Find every communication in which
the CFO discusses the special-purpose entities."

1. Request arrives: a lawyer types a search, or writes review instructions, or an Agent
   issues it as one step of a larger job. Searches and agent questions go through the
   Query Plane; review instructions may not, because aiR for Review reads every document
   rather than retrieving a shortlist.
2. The query: lexical search finds exact names and addresses; vector search finds
   passages that mean the same thing in other words. Lists are merged and reranked;
   anything the requester may not see is filtered out; each result comes back with the
   passage that matched.
3. Results: Knowledge / Metadata supplies custodian, sent date, participants, family and
   existing coding (for example privilege), and points to the originals in
   Files / Natives.
4. Relationships and meaning: Ontology lets an Agent follow connections ("who else was on
   these threads?").
5. Auditability: Data Capabilities logs who asked, what came back, and which model and
   prompt version scored each result.
6. The answer: results with citations (document, page, passage) and the record, which is
   what makes them defensible.

**Shipped versus mandated** (from comparing other postings and product documentation):

- Shipped retrieval (aiR Assist) is per-workspace "similarity search" over passages;
  "hybrid" does not appear in product documentation.
- Today's storage tiers are billing tiers (Staging, Repository, Review, Cold Storage),
  not a hot / warm / cold retrieval policy.
- At least three teams build their own retrieval today (Discovery, the Gavel agent team
  on Postgres, Assist). The Query Plane mandate is consolidation.
- The Enrichment organization (posting 26-0277, Principal Engineer) produces the data:
  "more than a petabyte a month," moving from batch to streaming on Kubernetes, Spark and
  Temporal, with Delta Lake, Iceberg, Parquet and Arrow.
- Generative inference is OpenAI models through Azure, region-pinned, with no fine-tuning
  for aiR for Review and one document per call.

## 2. An audit trail for agentic retrieval

Six steps, each with its actor:

1. A lawyer asks (human).
2. An agent plans searches (agent).
3. The Query Plane retrieves (system).
4. The agent reads each hit: decision, reason, quoted passage (agent).
5. The lawyer reviews flagged documents and samples the rejects (human).
6. A defensible result with citations and recall and elusion estimates (system).

Every step writes to an audit log: who asked and the exact wording; the plan with model
and prompt version; queries, filters and documents returned; the per-document decision,
reason, quote and model version; reviewer calls, overrides and sample sizes; the final
set with metrics and intervals.

Feedback loop: when the step-5 sample of rejects finds misses, the criteria are refined
and the run repeats from step 2. Three ways to audit that loop:

- **A. Versioned runs.** Each pass is a whole, immutable run (Run 1, Run 2, ...) with its
  criteria version, plan, results and metrics; runs are compared by diff.
- **B. Per-document lineage.** Each document carries its history across runs ("missed in
  Run 1, found in Run 2 under criteria v2, confirmed by reviewer").
- **C. Gated rerun.** A lawyer approves each criteria change before the rerun; the
  approval is its own logged event; metrics are reported on the final approved run, and
  earlier runs are kept as evidence.

The preference so far is A and B together, adding C where results will be challenged.

## 3. The aiR evaluation debate

- Redgrave LLP study (Relativity blog, 2026-06-09): about 45,000 documents from the UCSF
  Opioid Industry Documents Archive. aiR for Review: recall 88%, precision 29%, elusion
  1%. Active-learning review: 64%, 39%, 3%. About 18 against about 1,123 attorney hours.
- Cormack, Grossman and Pickens reanalysis (arXiv 2610.16925; eDiscovery Today,
  2026-10-15): the comparison was not like for like. With first-pass reviewers making the
  final call, active learning reached about 64% recall against 59.5% for aiR; with the
  system's calls standing, active learning 97.2% against about 88%, though they call the
  97% workflow likely disproportionate in cost. S-CAL reached about 100% estimated
  recall. Their conclusion: aiR is "comparable to, but not superior to" active learning,
  and it "offers only two viable recall-precision choices."
- Lessons: the dispute is about methodology, not raw numbers; human reviewers are the
  noisiest component (first-pass reviewers marked 34% of the expert's responsive
  documents non-responsive); tunable recall and precision is a product gap.

## 4. Nomenclature

**Metrics.** Never report "accuracy": at low richness, marking everything non-responsive
scores above 95%.

| Term | Meaning |
| --- | --- |
| richness | Share of the whole corpus that is responsive. Synonym: prevalence. |
| recall | Share of all responsive documents the system flagged. The completeness number courts care about. |
| precision | Share of flagged documents that are responsive. Drives review cost. |
| elusion | Rate of responsive documents inside the null set, measured by sampling it. Recall is usually estimated from elusion. |
| error rate | How often the system's call disagrees with a human reviewer's. Reported by aiR alongside the four above. |
| Clopper-Pearson 95% interval | Exact, conservative binomial interval; how Relativity reports every sampled metric. Always give n. |
| review effort | How many documents humans read to reach a given recall. The basis of "equal effort" comparisons. |

**Review and process terms**

| Term | Meaning |
| --- | --- |
| responsive | Meets the review criteria (formally: within the scope of the discovery request). Ground truth, set by a human or the gold set. |
| flagged | The system's prediction (in this repo: score >= 3). Not the same as responsive. |
| null set | The documents the system did not flag; where elusion is measured. Synonyms: discard pile, rejects, discards. |
| validation sample | The random sample humans review to measure the metrics; an elusion test samples the null set. |
| control set | A fixed hand-labeled set used to track progress (TAR 1.0 style). |
| technology-assisted review (TAR) | Umbrella term courts use for machine-assisted review. TAR 1.0 trains on a seed set, then stops; TAR 2.0, or CAL (continuous active learning), keeps learning from every reviewer decision. S-CAL is a scalable CAL variant. |
| high-recall retrieval | The research framing (Cormack, Grossman) for finding nearly all responsive documents at proportionate effort. |
| review criteria, prompt criteria | The written instructions an LLM review applies. Relativity's term in aiR for Review is "Prompt Criteria." |
| proportionality | The legal standard (FRCP 26(b)(1)) that effort must be reasonable relative to the stakes; why perfect recall is never required. No rule sets a recall threshold; about 70 to 80% is commonly argued as reasonable. |
| defensibility | Being able to show, later and to an adversary, what was done, by what, with what result and how sure you are. |
| audit trail | The log of decisions and actions: who asked, which model and prompt version, what was returned, who overrode what. |
| chain of custody | The provenance and integrity record for the documents themselves: where each came from, when, and proof it was not altered (for example hashes). |
| lineage | The trace from an answer back to the document, passage, model and criteria that produced it (wording of posting 26-0436). |
| matter, workspace, custodian, family, coding, privilege, hot documents | E-discovery domain nouns: a case; its data container; whose data it is; an email plus its attachments; reviewer labels; attorney-client or work-product protection; the few documents that decide a case. |

**Relativity platform terms** (from posting 26-0360 only; use as "Relativity's stated
vocabulary," not as facts about their systems): Relativity Intelligence Model,
Foundational Layer, Query Plane, Knowledge / Metadata, Files / Natives,
Ontology / Relationship, Data Capabilities, Skills, Agents, unified materialized document
layer, hybrid retrieval, reranking, managed chunking, tiered storage (cold / warm / hot).

## 5. Cautions for this repo's vocabulary

1. **"responsive", "relevant" and "flagged."** The metrics vocabulary in `CLAUDE.md` says
   "relevant"; the field says "responsive." Whichever word is used, it names the ground
   truth, and "flagged" names the system's prediction. Using them interchangeably means
   precision and recall get computed against the wrong thing. Adopting "responsive"
   repo-wide is undecided.
2. **"null set" and "discard pile."** The same thing. The repo's code and reports say
   "discard pile"; "null set" is the field's term. Standardizing on it is undecided.
3. **"chain of custody" and "audit trail."** Not interchangeable. Chain of custody is
   about the documents (provenance and integrity); the audit trail is about the
   decisions. For the phase 7 corpus, chain of custody means recording each abstract's
   OpenAlex work id, the harvest query, the harvest date and a content hash.
4. **"high-recall retrieval" and the phase 7 harvest.** Phase 7 adds a retrieval step
   before review: the harvest query. Anything the harvest never returned is not in the
   null set, so elusion cannot see it. Elusion measures the screen, not the harvest.
   Either report the harvest as out of scope for recall, or estimate harvest recall on
   its own, for example by checking a list of known on-topic papers against what was
   harvested.
5. **"gold set", "validation sample" and "control set."** The repo's gold set (an LLM
   judge's labels plus a small human audit) is closest to a validation sample. Say
   plainly that an LLM labeled it, and report judge-against-human agreement with its own
   interval.
6. **"criterion."** Fine internally. For readers from Relativity, "review criteria" or
   "Prompt Criteria" is their term.
7. **"accuracy."** Not in the vocabulary; keep it out of reports.
8. **Relativity's internal names** (Query Plane and the rest) appear in one job posting
   and nowhere in product documentation. Use them as framing, not as claims about
   Relativity's architecture.
