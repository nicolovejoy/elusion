# Phase 4: Leaderboard

<!-- Nico's draft. Four headings per docs/notes/README.md, half a page. Prompts in brackets are mine to overwrite. -->

## What I built

Five reviewers ran over the same 17,936 paragraphs: Haiku with three prompt versions,
OpenAI's small model gpt-6-luna, and a free local model, qwen2.5:14b, on my Mac. Each was
measured against the same second opinion (the Opus judge from phase 3), logged to MLflow as
one run per evaluation, and the best one was put on the registry shelf as `champion`. A new
check asked whether each reviewer's stated reason actually matched the paragraph it quoted.

Demo: `uv run elusion review --criterion criteria/cloud-concentration.md --reviewer elusion-reviewer@champion --sample 50`
then open http://localhost:5001 and compare the five runs side by side.

Finding: all five precision intervals overlap, so the winner (gpt-6-luna v2, 0.51) is a
choice, not a proof. The local model invented quotes in 4 of its 56 flags.

## What the tool is actually for

[MLflow in one line, in my words. Working version: a lab notebook where every attempt is
recorded with its settings and results, plus a shelf where one version gets the label "use
this one" and the pipeline can be pointed at the label instead of the model.]

[Registry and "promotion": a registered model version is a reviewer configuration (provider,
pinned model, prompt version) that can actually score; promotion is moving the `champion`
alias to it. Nothing is retrained; it is a pointer move with an audit trail.]

## What a production e-discovery platform does differently, and why

[Scale: five runs here; a platform compares reviewers per matter, per criterion, continuously.
Isolation: our judge is one Opus call; a platform's "second opinion" is a human QC sample
with its own defensibility record. Data Capabilities pillar: every model, prompt and
promotion decision auditable per tenant, not in one MLflow instance on a laptop. Cite the
posting (req 26-0360) or the aiR for Review validation docs if I find one.]

## The question I would ask their team

[Where judge and human disagreed: in phase 3 the judge and I disagreed on 4 of 20, all
score-3 paragraphs with no stated reliance on one provider. Prompt v3 added rules for those.
Question candidates: how do they pick the judge, and who audits the judge? Is "champion"
a per-matter decision or a platform one? What do they do when five reviewers' intervals all
overlap, as ours did?]
