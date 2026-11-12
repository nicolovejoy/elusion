# Phase 4: Leaderboard

## What we built

We ran five reviewers over the same roughly 18,000 paragraphs: Haiku with three different
prompts, OpenAI's small model gpt-6-luna, and qwen2.5:14b, a free open model running on the
dev Mac through Ollama (a program that runs models locally). Each was checked against the same
second opinion, the Opus judge from phase 3. gpt-6-luna with prompt v2 had the highest
precision, 0.51, and we labelled it `champion`. But the uncertainty ranges of all five
overlap: with only 42 to 75 flagged paragraphs each, the samples are too small to prove it
is better than the others. None of the five missed anything in a 200-paragraph sample of
its discards. The local model misquoted the paragraph in 4 of its 56 flags.

Demo: `uv run elusion review --criterion criteria/cloud-concentration.md --reviewer elusion-reviewer@champion --sample 50`
then open http://localhost:5001 and compare the five runs side by side.

## Why we built it

Phase 3 left us with one reviewer and one measured result. We wanted to know whether a
different model or a different prompt would do better, and whether a free model running
locally could compete with the paid ones. Answering that needs a way to keep attempts
comparable, which is the job MLflow exists for, so this was the phase to learn it by using
it. We also wanted to see what a model registry does when no model is being trained.

## What we learned

MLflow is a logbook for machine-learning experiments. Each time we try a version of a model,
it records the settings we used and how well that version scored. Traditionally the attempts
are training runs with different tuning. Here nothing is trained: each attempt is a different
combination of vendor model and prompt, five in all, and MLflow holds each one's precision,
recall and elusion side by side. It also lets us label one of them `champion`, so the
pipeline asks for "the champion" rather than naming a model, and changing which reviewer is
preferred means moving that label. The individual paragraph decisions are not in MLflow;
those live in Postgres.

This is not the traditional use of MLflow. Normally the registered model is a trained weights
file that can be reloaded exactly; ours is only a recipe (vendor, model snapshot, prompt
version) that works as long as the vendor keeps serving that snapshot. Using it this way is
deliberate: the project exists to get familiar with the tool.

## Results

We read the highest score as the best reviewer. With 42 to 75 flagged paragraphs per
reviewer, each precision figure is uncertain by 11 to 15 points either way, and all five
ranges overlap. gpt-6-luna against Haiku v3 or qwen is a tie on this evidence; against
the two weakest it is borderline. Halving that uncertainty takes about four times as many
flagged paragraphs.

Also: every number here uses the Opus judge as the truth, and in phase 3 the judge and a
human reading disagreed on 4 of 20 paragraphs. The measuring stick has its own error.
