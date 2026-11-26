# Concept audit: design

Status: drafted by Claude on 2026-11-18; **approved by the owner on 2026-11-26** with the web
approach below, and built the same day from `phase-7-concept-board-build.md`. It belongs
with the concept board (`phase-7-concept-board-map.md`) and uses its words.

**Changed by the owner on 2026-11-26** ("Decided by the owner, 2026-11-26" below): the
audit runs in a local web page and not in the terminal, the first read is made by a pinned
model, and "skip" is kept. Where an older line below says a terminal command, Claude in
the session, or asks whether skip is wanted, that block governs.

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

## Decided by the owner, 2026-11-26

The owner saw three mockups of the stopping-rules pass (a concept card, a paper note and
the blind audit screen) and called them good enough to build from. They are a private
canvas, not in this repo; the views are described under "The three views" below.

- **Order.** Part A of phase 7 is done and merged. One pass of the concept board on
  stopping rules comes next, before part B (`docs/DECISIONS.md`, 2026-11-26).
- **The audit runs in a local web page** (approach 3 below, in place of approach 1). The
  page shows a quote in place inside its abstract, which a terminal does poorly. It is
  local only, on the laptop, with one user and no login.
- **The first read is made by a pinned model through the pipeline**, not by Claude in the
  session. Each reading then carries a model snapshot id and a prompt version, like every
  other model call in the repo. This settles item 3 of "Not decided yet" in the board doc
  and replaces step 1 of "The design" below. The 2026-11-17 reading by Claude stays what
  it was: an unlogged first pass.
- **"Skip" is kept.** A skipped paper has no verdict; the disagreements step still waits
  until every paper has one, so a skipped paper comes round again.

**The web approach (approved 2026-11-26).** One Flask app started by
`uv run elusion board --concept stopping`, bound to localhost on port 8010, with
server-rendered HTML templates and no JavaScript. Every page is a view computed from
`concept_log`, and every button is one insert into it, so the page holds no state of its
own. Flask and not FastAPI, which was Claude's first proposal: FastAPI cannot read a
submitted HTML form without `python-multipart`, which is not installed, and Flask is
(through MLflow), reads forms itself and is synchronous like the repo's `psycopg` code.
The page refuses a request whose host is not localhost and a POST from another origin,
so no other website can post verdicts to it.

**The first read (approved 2026-11-26).** `claude-haiku-5-5` under prompt `first-read/v1`,
one call per abstract, through `uv run elusion first-read --concept stopping`. The owner
asked for the cheaper model; a quote that code cannot find verbatim in the abstract is
stored as a failed reading and not repaired.

**Held back.** Six of the 31 stopping abstracts are in the screen's gold draw, not one:
the draw grew to 800 after this page was written. All 31 are read by the model; the six
are kept off every page the owner sees until `elusion board --release-gold`, which is for
after his fresh 30 blind labels.

**The three views** (the mockups, in words).

1. *Concept card.* The wording as a question; counts (abstracts matching the words, how
   many hold the concept, how many use the words for something else, and the precision of
   the word search once audited); the rival answers side by side, each with its papers and
   verbatim quotes; links to other concepts, each with its quote; the rejected meanings
   with a quote each; our history from the log; the field's history by publication date.
   Every statement is marked one of two ways: the paper says so, or we decided so. A quote
   from a paper in the judge's gold draw is hidden until the owner's blind labels for the
   screen are done.
2. *Paper note.* The stored abstract with the quotes the reading rests on highlighted in
   place; the concepts the paper holds, each with its quote and the verbatim check; how
   the paper ties them together; who read it.
3. *Blind audit.* Step 1: the concept's wording, one abstract, and three buttons (holds,
   does not hold, skip), with nothing of the reader's shown. Step 2: only the
   disagreements, with both verdicts, the reader's quote, and one line of reason for the
   final verdict. At the end: agreement, and the precision of the word search with its
   interval.

**Flagging.** Beside the three buttons the audit page has a box, "Flag as interesting", and
an optional one-line note. The paper note has the same form, for papers already audited, and
lists the paper's flags. The card has a section, "Flagged as interesting", with each flagged
paper once and its notes. A flag is one `flag` row in `concept_log` (the note is in `body`),
is never taken back, and a held-back paper's flags are never shown. During the blind step
the owner can go back to the papers he has already seen (`/audit/seen`: skipped ones first,
his own outcome, his flags), flag them from a blind page that shows no part of the reading,
and settle a skip there; a given verdict is not changed.

**Next steps, in order.**

1. Done 2026-11-26: the approach approved; `concept_log`, the first read, the audit page,
   the concept card and the paper note built (`phase-7-concept-board-build.md`).
2. The owner records the concept's wording, runs the first read, and audits blind.
3. The owner settles the disagreements; the card and the paper notes open.
4. Then part B of the phase 7 spec (index, `ask`, `ask-eval`).

Still owed from part A: the owner's fresh 30 blind labels for the screen's judge. Six of
the 31 stopping abstracts are in that gold draw, so they are held back from anything the
owner reads until those labels are done.

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

- A hosted service, or anything on Azure.
- Auditing links between concepts. Only "does this paper hold this concept" is audited.
- Changing `elusion label`. It stays the audit of the screen's judge.

## What would make it a failure

- The owner sees Claude's verdict before giving his own.
- A verdict is stored without the version of the wording it was given against.
- An audit verdict reaches a prompt, a few-shot example or a retrieval context. The
  owner's verdicts are evaluation data and follow the rule for the gold set.
- An entry in the log is changed or deleted.

## Constraints

- Laptop only, from `compose.yaml`. No new service. One dependency is declared, `flask`,
  which was already installed through MLflow.
- The log is a Postgres table that takes only inserts (agreed proposal 3).
- The hard rules in `CLAUDE.md`. No model is called by the audit page; the first read is a
  separate command.

## Approaches

1. **A terminal command over the log table.** Picked on 2026-11-18, replaced by approach
   3 on 2026-11-26. It follows the pattern of
   `elusion label`, which the owner has used, and needs nothing new installed. Running in
   the terminal is Claude's call, not something the owner chose; he chose blind-then-open
   and the laptop.
2. **Extend `elusion label`.** Not picked. It draws its sample from the screen's judged
   rows and appends to the gold CSV. These 31 have no judged rows, and the audit's verdicts
   belong in the log.
3. **A local web page.** Picked on 2026-11-26. It would show a quote in place inside its
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
