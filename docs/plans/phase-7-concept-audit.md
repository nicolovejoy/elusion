# Concept audit: design

Status: drafted by Claude on 2026-11-18. **Not approved.** Nothing here is built, and nothing
is built until the owner approves this page. It belongs with the concept board
(`phase-7-concept-board-map.md`) and uses its words.

## Intent

The owner wants checking Claude's readings to be easy, and built in from the start instead
of added later. The first use is the 31 abstracts that contain the stopping words. Claude
counted 12 that hold the concept, and that count has two faults: the owner has not checked
it, and the 31 verdicts were never stored one by one. The board doc holds only the totals
and a few ids, so today there is nothing to audit.

## Decided by the owner (2026-11-18)

- The audit is blind first, then open on the disagreements only. Blind means the owner
  sees the abstract and the concept's wording and gives his own verdict, without Claude's.
  Open means he then sees Claude's verdict and quote, but only for the papers where the
  two differ, and settles each.
- It runs on the laptop. The project is not on Azure: the phase 6 cluster ran for under
  four hours on 2026-11-16 and was destroyed.

## Goals

1. Every reading is stored on its own before anyone audits it: the paper, the concept and
   the version of its wording, the verdict (holds, or does not hold), a verbatim quote, and
   who read it.
2. The owner audits the 31 blind, then settles the disagreements.
3. A figure comes out: how often the two agree, and the precision of the word search (how
   many of the 31 hold the concept by the owner's verdicts) with its Clopper-Pearson 95%
   interval and the sample size.
4. Each of the owner's verdicts is itself an entry in the log, so the "says who?" test has
   an answer for it: we decided so.

## Not goals

- A web page, a hosted service, or anything on Azure.
- Auditing links between concepts. Only "does this paper hold this concept" is audited.
- Changing `elusion label`. It stays the audit of the screen's judge.
- Deciding who does first reads from now on, a pinned model or Claude in the session. That
  is item 3 under "Not decided yet" in the board doc.

## What would make it a failure

- The owner sees Claude's verdict before giving his own.
- A verdict is stored without the version of the wording it was given against.
- An audit verdict reaches a prompt, a few-shot example or a retrieval context. The
  owner's verdicts are evaluation data and follow the rule for the gold set.
- An entry in the log is changed or deleted.

## Constraints

- Laptop only, from `compose.yaml`. No new service and no new dependency.
- The log is a Postgres table that takes only inserts (agreed proposal 3).
- The hard rules in `CLAUDE.md`. No model is called by the audit itself.

## Approaches

1. **A terminal command over the log table.** Picked. It follows the pattern of
   `elusion label`, which the owner has used, and needs nothing new installed. Running in
   the terminal is Claude's call, not something the owner chose; he chose blind-then-open
   and the laptop.
2. **Extend `elusion label`.** Not picked. It draws its sample from the screen's judged
   rows and appends to the gold CSV. These 31 have no judged rows, and the audit's verdicts
   belong in the log.
3. **A local web page.** Not picked for now. It would show a quote in place inside its
   abstract, which a terminal does poorly, but it is more to build and adds a dependency.
   It is the likely next step once the board holds more than one concept.
4. **A hosted page on Azure.** Not picked. There is one auditor and the data is in the
   local Postgres, so hosting adds a login and a cloud database for no gain. A small page
   can run nearly free; the smallest managed Postgres is roughly $13 a month (from memory,
   not checked).

## The design

**The log.** One table, `concept_log`, append-only. Each row is one entry of one kind:

- `concept`: a concept is created or reworded. Holds its id, the version and the wording.
- `reading`: one paper read against one version of one concept. Holds the verdict, the
  quote, and the reader: a model snapshot with its prompt version, or "Claude in the
  session, unpinned".
- `audit`: the owner's blind verdict on one paper for one version of one concept.
- `decision`: something the owner and Claude settled, with its reason and the entries it
  rests on. A disagreement settled in the open step is a decision.

Every row carries when it was written, in UTC. Updates and deletes are refused by the
database, not only by the code.

**Step 1: write the readings down.** Claude reads the 31 again and stores one `reading`
entry for each, with a quote that code confirms is verbatim in the stored abstract. The
reader is recorded as Claude in the session, unpinned. This is a second reading, so it may
not come to 12 and 19 again; any difference from the 2026-11-17 count is reported, not
hidden.

**Step 2: the blind audit.**

```
uv run elusion audit --concept stopping
```

It shows the concept's wording once, then one abstract at a time, title and text, in a
seeded random order. The owner answers holds, does not hold, or skip. Each answer is
stored as an `audit` entry at once, so the command can be stopped and started again and
picks up where it left off. It never prints a reading.

**Step 3: the disagreements.**

```
uv run elusion audit --concept stopping --disagreements
```

For each paper where the owner's verdict differs from Claude's, it shows both verdicts and
Claude's quote. The owner gives the final verdict and one line of reason, stored as a
`decision` entry. It refuses to start while any of the 31 has no blind verdict.

**What it prints at the end.** The counts, the agreement between the two, and the precision
of the word search with its interval. It prints to the terminal only; a section in a
report can come later.

**Tests.** The table refuses an update and a delete. A reading whose quote is not in the
stored abstract is refused. The blind step prints no reading, verdict or quote of
Claude's. A stopped audit resumes without asking for the same paper twice. Step 3 refuses
to start early.

## Left to the builder

- The exact columns and types of `concept_log`, and how the append-only rule is enforced.
- How a concept is named on the command line.
- Whether "skip" papers come round again at the end of the same sitting.

## Open, for the owner

1. Is "skip" wanted at all, or must every paper get a verdict?
2. The size of a concept, which the owner put off to the next session. This design works
   with any answer, because it audits one concept at whatever size it was worded.
