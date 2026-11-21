"""How often the judge and the owner agree, read from the gold CSV alone.

The judge model's labels stand in for ground truth, so they are only worth as much as
their agreement with a person. `evaluate` prints that agreement in the report, but only
once the whole gold set is labeled. This module answers the earlier question: the owner
has labeled a few dozen blind, the judge has labeled the same ones (`elusion judge
--only-labeled`), and before the judge is paid for the rest we want the count, its
interval and every abstract the two disagree on.

It makes no model call and writes nothing. The gold labels are printed to the terminal
for the owner; they go into no prompt.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

from elusion.evaluate import metrics
from elusion.evaluate.gold import human_rows, judge_rows_for
from elusion.evaluate.metrics import Estimate


@dataclass(frozen=True)
class Disagreement:
    doc_id: str
    pile: str  # the pile the owner's row recorded
    owner: int
    judge: int
    rationale: str  # the judge's reason


@dataclass(frozen=True)
class Comparison:
    estimate: Estimate  # k agreeing out of n labeled by both, Clopper-Pearson interval
    human_only: int  # owner labels the judge model has not labeled yet (not counted)
    disagreements: list[Disagreement]


def compare(path: Path, judge_model: str, prompt_version: str) -> Comparison:
    """Compare the owner's rows with those `judge_model` wrote under the judge prompt
    `prompt_version` ("<criterion>/judge-vN"), over the abstracts both labeled.

    Raises ValueError when there is none (nothing labeled by the owner, or the judge
    has not read any of it yet).
    """
    humans = human_rows(path)
    judged = {r.doc_id: r for r in judge_rows_for(path, judge_model, prompt_version)}
    estimate = metrics.agreement(humans, list(judged.values()))
    shared = [h for h in humans if h.doc_id in judged]
    disagreements = [
        Disagreement(h.doc_id, h.pile, h.label, judged[h.doc_id].label, judged[h.doc_id].rationale)
        for h in sorted(shared, key=lambda r: r.doc_id)
        if h.label != judged[h.doc_id].label
    ]
    return Comparison(estimate, len(humans) - len(shared), disagreements)


def render(
    c: Comparison,
    judge_model: str,
    texts: dict[str, str],
    width: int = 80,
    prompt_version: str = "",
) -> str:
    """The comparison as plain text: one line of count, then each disagreement in full."""
    e = c.estimate
    yn = {1: "yes", 0: "no"}
    lines = [
        f"agreement: {e.k} of {e.n} ({e.point:.3f}), 95% interval [{e.low:.3f}, {e.high:.3f}]",
        f"judge: {judge_model}" + (f", prompt {prompt_version}" if prompt_version else ""),
    ]
    if c.human_only:
        one = c.human_only == 1
        lines.append(
            f"{c.human_only} of the owner's labels {'has' if one else 'have'} no "
            f"{judge_model} label yet and {'is' if one else 'are'} not counted"
        )
    if not c.disagreements:
        return "\n".join([*lines, "", "No disagreement."]) + "\n"
    lines += ["", f"{len(c.disagreements)} disagreements:"]
    for d in c.disagreements:
        lines += [
            "",
            f"--- {d.doc_id} ({d.pile}): owner {yn[d.owner]}, judge {yn[d.judge]}",
            textwrap.fill(f"judge: {d.rationale}", width=width),
            "",
            textwrap.fill(texts.get(d.doc_id, "(text not found in the corpus)"), width=width),
        ]
    return "\n".join(lines) + "\n"
