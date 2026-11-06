"""Review metrics with confidence intervals: precision, elusion, richness, recall.

A Clopper-Pearson interval is an exact binomial confidence interval computed from the beta
distribution: given k hits in n samples it says which true rates are still plausible at
95%. It never goes below 0 or above 1 and is conservative (wider than strictly needed)
at small n, which is the honest choice for a gold set of a few hundred labels.

Precision (relevant rate among flagged) and elusion (relevant rate among discarded) are
measured directly, by labeling the flagged pile and a random sample of the discard pile.
Richness (relevant rate in the whole population) and recall (share of all relevant
paragraphs that were flagged) are derived from those two, weighted by pile sizes: recall
cannot be measured directly because finding all relevant paragraphs is the very thing
the review is trying to do. Derived intervals come from evaluating the formula at the
bounds of the two input intervals, so they are not exact 95% intervals.

Convention: a derived `Estimate` (richness, recall) has k = -1 and n = population size.
"""

from __future__ import annotations

from dataclasses import dataclass

from scipy.stats import beta

from elusion.evaluate.gold import GoldRow


@dataclass(frozen=True)
class Estimate:
    point: float
    low: float
    high: float
    k: int  # successes; -1 marks a derived estimate (richness, recall)
    n: int  # sample size, or population size for derived estimates


def clopper_pearson(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    if n == 0:
        raise ValueError("clopper_pearson needs n > 0")
    alpha = 1 - confidence
    low = 0.0 if k == 0 else float(beta.ppf(alpha / 2, k, n - k + 1))
    high = 1.0 if k == n else float(beta.ppf(1 - alpha / 2, k + 1, n - k))
    return low, high


def proportion(k: int, n: int) -> Estimate:
    """k successes out of n with a Clopper-Pearson interval; n must be positive."""
    if n == 0:
        raise ValueError("proportion needs n > 0")
    low, high = clopper_pearson(k, n)
    return Estimate(k / n, low, high, k, n)


def _proportion(rows: list[GoldRow], pile: str) -> Estimate:
    if not rows:
        raise ValueError(f"no labeled rows in the {pile} pile")
    return proportion(sum(1 for r in rows if r.label == 1), len(rows))


def precision(flagged_rows: list[GoldRow]) -> Estimate:
    return _proportion(flagged_rows, "flagged")


def elusion(discard_rows: list[GoldRow]) -> Estimate:
    return _proportion(discard_rows, "discard")


def _richness_at(p: float, e: float, n_flagged: int, n_discard: int) -> float:
    return (p * n_flagged + e * n_discard) / (n_flagged + n_discard)


def _recall_at(p: float, e: float, n_flagged: int, n_discard: int, fallback: float) -> float:
    rel_flagged, rel_discard = p * n_flagged, e * n_discard
    total = rel_flagged + rel_discard
    return fallback if total == 0 else rel_flagged / total


def _clamp(x: float) -> float:
    return min(1.0, max(0.0, x))


def richness(precision: Estimate, elusion: Estimate, n_flagged: int, n_discard: int) -> Estimate:
    n = n_flagged + n_discard
    return Estimate(
        _clamp(_richness_at(precision.point, elusion.point, n_flagged, n_discard)),
        _clamp(_richness_at(precision.low, elusion.low, n_flagged, n_discard)),
        _clamp(_richness_at(precision.high, elusion.high, n_flagged, n_discard)),
        -1,
        n,
    )


def recall(precision: Estimate, elusion: Estimate, n_flagged: int, n_discard: int) -> Estimate:
    """Recall is undefined when no paragraph is relevant at all (0/0). Then the point is
    0.0 with the interval [0, 1]; a bound whose own inputs give 0/0 falls back the same
    way (0.0 for the low bound, 1.0 for the high bound)."""
    n = n_flagged + n_discard
    return Estimate(
        _clamp(_recall_at(precision.point, elusion.point, n_flagged, n_discard, 0.0)),
        _clamp(_recall_at(precision.low, elusion.high, n_flagged, n_discard, 0.0)),
        _clamp(_recall_at(precision.high, elusion.low, n_flagged, n_discard, 1.0)),
        -1,
        n,
    )


def agreement(human_rows: list[GoldRow], judge_rows: list[GoldRow]) -> Estimate:
    judge = {r.doc_id: r.label for r in judge_rows}
    shared = [(r.label, judge[r.doc_id]) for r in human_rows if r.doc_id in judge]
    if not shared:
        raise ValueError("no doc_ids labeled by both the human and the judge")
    k = sum(1 for h, j in shared if h == j)
    n = len(shared)
    low, high = clopper_pearson(k, n)
    return Estimate(k / n, low, high, k, n)
