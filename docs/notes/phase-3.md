# Phase 3: Validation

## What I built

The 20,000-id sample was rescored under a tightened criterion (prompt v2), claude-opus-5-5 labeled
every flagged paragraph plus 200 random discards, I audited 20 of its labels blind, and a report
generator writes one page with the four metrics. Demo:
`uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2`.

- Population: 17,936 paragraphs scored under (claude-haiku-5-5, v2). Scores 0: 14,759; 1: 2,908;
  2: 194; 3: 63; 4: 12. Flagged (score >= 3): 75. Phase spend $2.0514.
- precision 0.333 [0.229, 0.452] (25/75); elusion 0.000 [0.000, 0.018] (0/200); richness 0.001
  [0.001, 0.020] derived; recall 1.000 [0.050, 1.000] derived; judge/owner agreement 0.800
  [0.563, 0.943] (16/20).
- An LLM judged an LLM. My 20 labels, auditing the judge, are the only human ground truth.

## What the tool is actually for

<!-- draft: owner to rewrite in own words -->
- Recall: of all the paragraphs that really are about leaning on someone else's cloud, how many
  did the review catch?
- Precision: of the paragraphs the review flagged, how many really were about that, and not just
  "banks have computers"?
- Elusion: what share of the discard pile should have been kept?
- Richness: how rare the thing is. Here about one paragraph in a thousand.

A Clopper-Pearson 95% interval is the range of true rates that could plausibly give k hits in n
draws. Elusion is sampled from the discard pile; recall cannot be sampled (nobody knows in advance
which paragraphs are relevant), so it is derived from the two piles.

Why recall's interval is uninformative. Zero hits in 200 still allows elusion up to 1.8%, and 1.8%
of 17,861 discards is about 320 paragraphs that could be relevant, against 25 found. The point
estimate 1.000 is an artefact of k=0. Bounding misses below 25 (recall of at least about 50%) needs
roughly 2,500 discard labels, about $6.50 at the judge's rate. Not done and not decided.

Precision by score: score 4, 9/12 relevant (75%); score 3, 16/63 (25%). Flagging only score 4 would keep 9 of the 25 known hits and lose 16. Whether to fix that
with a tighter criterion or a different threshold is open.

Where the owner and the judge disagreed. Discard half: 10/10, all trivial no's, so at n=20 the
audit cannot catch judge misses there. Flagged half: 6/10, all four misses me yes, judge no, on
score-3 paragraphs:

- 53e7f1782c6061b9: cloud-based SaaS licensed from third parties.
- ca845d50310c7329: information systems and data processing outsourced to unnamed third parties.
- a5acc3f20ec6c380 (DJO): mixed on-site and cloud data centers, vendors manage "parts".
- c2248fcef312aad9: generic third-party hardware and software for an e-commerce site.

None states reliance on one or a few providers. The cause was my instructions: while I labeled,
the controller gave me a one-line paraphrase ("is someone else running this company's servers,
and is that a risk?") that dropped the criterion's concentration requirement. On review I agree
the judge was right on most; labels were not changed. The lesson: an audit measures the
labeler's instructions as much as the labeler; a paraphrased protocol is a different protocol. Agreement between two professional review teams is
commonly cited at around 70-75% (Roitblat, Kershaw & Oot 2010).

Criterion gaps that labeling surfaced, v3 candidates: SaaS vendors; ISPs (count only if they also
host or colocate); generic IT outsourcing; mixed on-site plus cloud setups.

## What a production e-discovery platform does differently, and why

Scale: the validation sample is typically sized to the richness and the recall claim, not fixed
at 200. Isolation: keeping each matter's protocol and gold set separate is a common practice.
Defensibility: one written protocol, given verbatim to every reviewer, human or model, and a
validation method agreed in advance; court orders such as the TAR validation protocol in
*In re Broiler Chicken Antitrust Litigation* (N.D. Ill. 2018) specify an elusion sample and how
recall is estimated from it.

What a skeptical reader would ask:

- Who decided relevance? An LLM, checked by 20 human labels.
- How exact are the intervals? Precision is a census of all 75 flagged, so its interval only
  loosely stands for label noise. Richness and recall combine two independent 95% intervals, so
  they cover at least about 90%. The discard sample is uniform only because the gold set was drawn
  from the same v2 scores it is evaluated against.
- Which companies? None: `documents.company` is NULL for all 1,604,015 rows. EDGAR-CORPUS has a
  CIK but no name, and phase 1 never filled it. Fix (SEC CIK-to-name list) not done or decided.

## The question I would ask their team

When richness is around 0.1% and a zero-hit elusion sample of a few hundred still leaves recall
between 5% and 100%, how does your platform prove recall to opposing counsel, and who labels
that sample?
