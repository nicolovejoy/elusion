# Phase 3: Validation

## What I built

The 20,000-id sample was rescored under a tightened criterion (prompt v2), claude-opus-5-5 labeled
a gold set of every flagged paragraph plus 200 random discards, I audited 20 of its labels blind,
and a report generator turns all of it into one page with recall, precision, elusion and richness.
Demo: `uv run elusion evaluate --criterion criteria/cloud-concentration.md --prompt-version v2`,
which writes `docs/reports/cloud-concentration-claude-haiku-5-5-v2.md`.

What ran:

- Review v2 (job c2c744f9, claude-haiku-5-5, `cloud-concentration/v2`): 20,000 pulled, 17,936
  scored, 2,064 skipped (ids published twice on the topic), 0 failed, $1.3422 against $1.6478
  projected, 0:55:46 wall-clock (the plan guessed about 25 minutes).
- Judge (Prefect run "eminent-starfish", claude-opus-5-5, `cloud-concentration/judge-v1`, effort
  low): 275 labels, all 75 flagged plus 200 discard drawn with seed 20261104, 0 failed, $0.7093
  against $0.9706 projected.
- Owner audit: 20 blind labels, 10 per pile. Phase total: $2.0514.

What came out. The population is the 17,936 paragraphs scored under (claude-haiku-5-5, v2): 75
flagged at score >= 3 (12 scored 4, 63 scored 3), 17,861 discard.

- precision 0.333 [0.229, 0.452] (25/75)
- elusion 0.000 [0.000, 0.018] (0/200)
- richness 0.001 [0.001, 0.020], derived
- recall 1.000 [0.050, 1.000], derived
- judge/owner agreement 0.800 [0.563, 0.943] (16/20)

Ground truth here is an LLM: Opus judged Haiku. My 20 labels are the only human ground truth in
the phase, and they audit the judge, not the review.

## What the tool is actually for

<!-- draft: owner to rewrite in own words -->
- Recall: of all the paragraphs that really are about leaning on someone else's cloud, how many
  did the review catch?
- Precision: of the paragraphs the review flagged, how many really were about that, and not just
  "banks have computers"?
- Elusion: go through the pile the review threw away and count how many it should have kept.
- Richness: how rare the thing is in the first place. Here about one paragraph in a thousand.

The terms behind them:

- Clopper-Pearson 95% interval: the range of true rates that could plausibly have produced k hits
  in n draws. It is exact and conservative, and still works when k is 0, which is the case that
  matters here.
- Gold set, judge, audit: the gold set is the answer key. The judge is a stronger model writing
  most of the key. The audit is my blind labels on a few of the same paragraphs, which measure the
  judge.
- Two piles: precision is a census (every flagged paragraph was judged); elusion is a sample of
  the discard pile. Recall and richness cannot be sampled directly, because nobody knows in advance
  which paragraphs are relevant, so they are derived: relevant in the discard pile is estimated as
  elusion times 17,861, and recall is found relevant over found plus missed.

Why recall's interval is uninformative. Zero hits in 200 discards still allows elusion up to 1.8%,
and 1.8% of 17,861 is about 320 relevant paragraphs that could be sitting in the discard pile,
against the 25 found. So recall is anywhere from 5% to 100%. The point estimate 1.000 is an
artefact of k=0; the interval is the honest number. At about 0.1% richness, a small discard sample
cannot prove recall. Bounding the misses below 25 (recall of at least about 50%) needs an elusion
upper bound of 25/17,861, about 0.14%, which with zero hits means roughly 2,500 discard labels
(the ~3.7/n rule for a zero-hit Clopper-Pearson bound), about $6.50 at the judge's $0.0026 per
label. That is a limit of this run, not something done; I have not decided whether to buy it.

Precision by score says the threshold is doing work. Score 4: 9/12 relevant (75%). Score 3: 16/63
(25%). Almost every false alarm is a 3, but so are 16 of the 25 hits, so moving the line to 4
would trade most of the recall for precision. A tighter criterion looks like the better lever.

Where the owner and the judge disagreed. The discard half was 10/10, all trivial no's, so with
n=20 the audit cannot detect judge misses in the discard pile; it measures overall agreement. The
flagged half was 6/10. All four disagreements were me yes, judge no, all on score-3 paragraphs:

- 53e7f1782c6061b9: cloud-based SaaS licensed from third parties.
- ca845d50310c7329: information systems and data processing outsourced to unnamed third parties.
- a5acc3f20ec6c380 (DJO): a mix of on-site and cloud data centers, vendors manage "parts"; a data
  security risk factor.
- c2248fcef312aad9: generic third-party hardware and software providers for an e-commerce site.

None states reliance on a single provider or a small number of them. The cause was my
instructions, not my judgment: while I labeled, the controller gave me a one-line paraphrase ("is
someone else running this company's servers, and is that a risk?") that dropped the criterion's
concentration requirement ("a single ... or a small number of such providers"). After review I
agree the judge was right on most of them. The labels were not changed. The lesson: an audit
measures the labeler's instructions as much as the labeler, and a paraphrased protocol is a
different protocol. For scale, agreement between two professional review teams in published
e-discovery studies is commonly cited at around 70-75% (Roitblat, Kershaw & Oot 2010, JASIST).

Criterion gaps that labeling surfaced, none covered by the v2 "counts / does not count" lists, so
they are v3 candidates: SaaS vendors; ISPs and connectivity providers (count only if they also
host or colocate); generic IT outsourcing; mixed on-site plus cloud setups. My own questions while
labeling show the same gaps: "relevant means having to do with cloud providers?", "are ISPs cloud
providers?".

## What a production e-discovery platform does differently, and why

Scale: the validation sample is sized to the richness and the recall claim, often thousands of
documents, not a fixed 200. Isolation: the protocol and gold set belong to one matter and are
never reused across matters. Defensibility: the review protocol is a written document given
verbatim to every reviewer, human or model, and the validation method is agreed in advance; court
orders such as the TAR validation protocol in *In re Broiler Chicken Antitrust Litigation*
(N.D. Ill. 2018) specify an elusion sample and how recall is estimated from it.

What a skeptical reader would ask:

- Who decided relevance? An LLM. 20 human labels, agreement 0.800 [0.563, 0.943], and the four
  misses came from my paraphrased instructions.
- Is recall proven? No: [0.050, 1.000]. See the ~2,500-label arithmetic above.
- Why does precision have an interval at all? All 75 flagged paragraphs were judged, so 25/75 is a
  census of the judge's answers with no sampling error over the pile. The interval stands, loosely
  at best, for uncertainty in the labels themselves.
- Are the derived intervals 95%? No. Richness and recall combine two independent 95% intervals,
  so they cover the true value at least about 90% of the time (0.95 x 0.95).
- Is the discard sample random? Uniform only because the gold set was drawn from the same v2
  scores it is evaluated against. A different scoring run needs its own discard sample.
- Is this the corpus? No. Richness and recall describe the population: the first 20,000 topic ids
  in ingest order (17,936 distinct), not a random draw from 1.6 million paragraphs.
- Which companies? The report's "companies found" is blank: `documents.company` is NULL for all
  1,604,015 rows. EDGAR-CORPUS carries a CIK but no company name, phase 1 never filled it, and it
  was not documented until now. The fix (the SEC CIK-to-name list) is not done and not decided.

## The question I would ask their team

On a low-richness population, where a zero-hit elusion sample of a few hundred still leaves recall
anywhere between 5% and 100%, how does your platform prove recall to opposing counsel: by sizing
the elusion sample to the richness, by stratifying it across score bands, or by some other design,
and who labels that sample?
