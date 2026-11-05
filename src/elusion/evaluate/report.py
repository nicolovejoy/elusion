"""One Markdown page per review run: the artifact the project exists to produce.

A `RunReport` holds every number the page shows; `render` turns it into Markdown and does
no I/O, so the page can be tested from a fixed set of values. The evaluate flow
(flows/evaluate.py) gathers the values from Postgres and the gold CSV and writes the file.

Measured metrics (precision, elusion, judge agreement) print their sample as k/n: k
relevant (or agreeing) out of n labeled. Derived metrics (richness, recall) have no
sample of their own; they combine the two pile intervals weighted by pile sizes, and
say so instead of printing a k/n.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from elusion.evaluate.metrics import Estimate
from elusion.review.providers import EFFORT


@dataclass(frozen=True)
class RunReport:
    criterion: str
    review_model: str
    prompt_version: str  # review prompt version, "<criterion>/<version>"
    judge_model: str
    judge_prompt_version: str
    date: str  # ISO date the report was produced
    seed: int
    discard_n: int  # size of the seeded discard draw
    threshold: int
    population: int  # every paragraph scored under (review_model, prompt_version)
    n_flagged: int
    n_discard: int
    precision: Estimate
    elusion: Estimate
    richness: Estimate
    recall: Estimate
    agreement: Estimate | None  # None when the owner has not labeled anything yet
    review_cost_usd: float
    judge_cost_usd: float
    review_jobs: int  # review_jobs rows for the pair
    review_wall_clock: timedelta | None  # None when there are no review_jobs rows
    review_unfinished: bool  # a job has no finished_at: wall-clock runs to now
    companies: list[tuple[str, int]]


def _interval(e: Estimate) -> str:
    return f"{e.point:.3f} [{e.low:.3f}, {e.high:.3f}]"


def metric_line(name: str, e: Estimate) -> str:
    if e.k < 0:  # derived estimate: k = -1 is a sentinel, n is the population
        return f"- {name}: {_interval(e)}, derived from the two pile intervals"
    return f"- {name}: {_interval(e)} (k/n = {e.k}/{e.n})"


def _duration(d: timedelta) -> str:
    return str(d - timedelta(microseconds=d.microseconds))


def render(r: RunReport) -> str:
    lines = [
        f"# Review report: {r.criterion}",
        "",
        f"- criterion: {r.criterion}",
        f"- review model: {r.review_model}",
        f"- review prompt version: {r.prompt_version}",
        f"- judge model: {r.judge_model}",
        f"- judge prompt version: {r.judge_prompt_version}",
        f"- judge effort: {EFFORT}",
        f"- date: {r.date}",
        f"- seed: {r.seed}",
        "",
        "## Population",
        "",
        "Every paragraph scored under the review model and prompt version above.",
        "",
        f"- population N: {r.population}",
        f"- flagged (score >= {r.threshold}): {r.n_flagged}",
        f"- discard (score < {r.threshold}): {r.n_discard}",
        f"- threshold: {r.threshold}",
        f"- discard sample: {r.discard_n} drawn with seed {r.seed} from the pair's discard pile; "
        f"{r.elusion.n} labeled by the judge",
        "",
        "## Metrics",
        "",
        "Point estimate and Clopper-Pearson 95% interval; ground truth is the judge's label.",
        "",
        metric_line("precision", r.precision),
        metric_line("elusion", r.elusion),
        metric_line("richness", r.richness),
        metric_line("recall", r.recall),
        "",
        "- precision: fraction of flagged paragraphs that are truly relevant.",
        "- elusion: relevant rate inside the discard pile, measured by sampling it.",
        "- richness: fraction of the whole population that is relevant.",
        "- recall: fraction of truly relevant paragraphs the system flagged.",
        "",
        "Richness and recall are not measured on a sample of their own: they combine the",
        "precision and elusion intervals weighted by pile sizes. Both are monotone in",
        "precision and elusion, and the two samples are independent, so each derived",
        "interval covers the true value with probability at least about 90% (0.95 × 0.95),",
        "not 95%.",
        "",
        "## Judge agreement with the owner",
        "",
    ]
    if r.agreement is None:
        lines.append("No human audit labels yet; run `elusion label` to add some.")
    else:
        lines += [
            "Fraction of the owner's blind labels the judge matched.",
            "",
            metric_line("agreement", r.agreement),
        ]
    lines += [
        "",
        "## Cost",
        "",
        f"- review run: ${r.review_cost_usd:.4f} (sum over {r.review_jobs} review_jobs rows)",
        f"- judge run: ${r.judge_cost_usd:.4f} (sum over the judge-runs ledger)",
        f"- total: ${r.review_cost_usd + r.judge_cost_usd:.4f}",
        "",
        "## Wall-clock",
        "",
    ]
    if r.review_wall_clock is None:
        lines.append("- review run: no review_jobs rows for this model and prompt version")
    else:
        end = "now (a job has not finished)" if r.review_unfinished else "last finish"
        lines.append(
            f"- review run, first start to {end} across {r.review_jobs} review_jobs rows: "
            f"{_duration(r.review_wall_clock)}"
        )
    lines += [
        "",
        "## Companies found",
        "",
        "Companies of the flagged paragraphs the judge labeled relevant, with paragraph counts.",
        "",
    ]
    if r.companies:
        lines += [f"- {name}: {count}" for name, count in r.companies]
    else:
        lines.append("No flagged paragraph was labeled relevant by the judge.")
    return "\n".join(lines) + "\n"
