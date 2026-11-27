# Phase 7: The literature matter

<!-- Draft; the owner edits it. -->

## What we built

A second corpus about the project's own subject: recent research on AI agents whose
decisions must be auditable. Part A harvested 23,521 abstracts from OpenAlex (an open
catalogue of scholarly works), each stored with a chain-of-custody record, and screened them
with the same review, judge and evaluate flows as the 10-K matter. The concept board
recorded a concept's wording, searched for its words and had the owner audit the model's
first reads blind.

Part B makes the 10,034 screened-in abstracts answerable. `elusion index` stores each one
with an *embedding*: a list of 1,536 numbers a model makes for a text, so that texts of
similar meaning sit close together. `elusion ask` retrieves five abstracts by one of three
*arms* (by words, `lexical`; by meaning, `vector`; both merged, `hybrid`), and one model
call answers in claims that each carry a quote. Python checks every quote word for word.
`elusion ask-eval` puts 30 gold questions through each arm and has a second model judge
each claim.

## Why we built it

To see whether the screen carries over to another kind of document, and to learn retrieval
by measuring it: which way of searching finds the right abstract, and how far an answer
built from quotes can be trusted.

## What we learned

<!-- The owner writes this in his own words. -->

## Results

Part A, the screen (gpt-6-luna, prompt v1, 23,485 abstracts screened, 10,577 flagged;
ground truth is the judge's label on 398 flagged and 400 discarded):

- precision 0.734 [0.687, 0.776]; elusion 0.295 [0.251, 0.342]
- recall 0.671 [0.622, 0.717]; richness 0.493 [0.447, 0.538] (both derived, about 90% cover)
- harvest recall 0.256 [0.181, 0.344] (31 of 121): the search terms returned about a
  quarter of the on-topic works found by another route. Elusion cannot see this.

Part B, `ask-eval` (25 answerable questions, 5 unanswerable, k = 5, Clopper-Pearson 95%):

| Arm | recall@5 | groundedness by question | quote check failed |
|---|---|---|---|
| lexical | 16 of 25, 0.640 [0.425, 0.820] | 5 of 18, 0.278 [0.097, 0.535] | 2 of 25 |
| vector | 12 of 25, 0.480 [0.278, 0.687] | 2 of 17, 0.118 [0.015, 0.364] | 2 of 25 |
| hybrid | 17 of 25, 0.680 [0.465, 0.851] | 2 of 21, 0.095 [0.012, 0.304] | 8 of 25 |

Every arm refused all 5 unanswerable questions (5 of 5, [0.478, 1.000]). `hybrid` stays
the default: against it, `lexical` won 2 questions and lost 3, `vector` won 2 and lost 7,
and the rule needs a lead of 3.

How far to trust this:

- The first run gave `lexical` 0 of 25. That was a defect, not a finding: its ranking
  rewarded long documents. It was fixed and the arm measured again; the first report is
  kept beside the second.
- 25 questions give wide intervals. No arm is shown to be better than another.
- A model drafted the questions and a model chose the 25 and wrote the 5 unanswerable
  ones. Each question is a long paraphrase of one abstract, which is easier to retrieve
  than what a person would type.
- recall@5 is a lower bound: another abstract may also answer the question.
- Groundedness is low because the judge compares a claim with its one quote and rejects
  anything the quote alone does not say. Nobody has checked the judge against a person.
- A failed quote check is the check working: the model returned a quote that is not word
  for word in the abstract, often with a stray tag at its end, and the answer is not shown.

The words, across three fields:

| This repo | Systematic review | E-discovery |
|---|---|---|
| review (the screen) | screening | first-pass review |
| flagged, screened-in | included | responsive |
| discard pile | excluded | null set |
| elusion | (no usual word) | elusion |
