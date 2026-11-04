import pytest

from elusion.evaluate.gold import GoldRow
from elusion.evaluate.metrics import (
    Estimate,
    agreement,
    clopper_pearson,
    elusion,
    precision,
    recall,
    richness,
)


def row(doc_id: str, label: int, pile: str = "flagged", labeler: str = "judge") -> GoldRow:
    return GoldRow(doc_id, label, pile, labeler, "v2", "", "2026-11-04T00:00:00+00:00")


def test_clopper_pearson_zero_successes():
    low, high = clopper_pearson(0, 20)
    assert low == 0.0
    assert high == pytest.approx(0.1684, abs=1e-4)


def test_clopper_pearson_all_successes():
    low, high = clopper_pearson(20, 20)
    assert low == pytest.approx(0.8316, abs=1e-4)
    assert high == 1.0


def test_clopper_pearson_known_values():
    low, high = clopper_pearson(3, 10)
    assert low == pytest.approx(0.0667, abs=1e-3)
    assert high == pytest.approx(0.6525, abs=1e-3)


def test_clopper_pearson_empty_raises():
    with pytest.raises(ValueError):
        clopper_pearson(0, 0)


def test_precision_and_elusion():
    p = precision([row("a", 1), row("b", 1), row("c", 0), row("d", 0)])
    assert (p.k, p.n, p.point) == (2, 4, 0.5)
    assert p.low < 0.5 < p.high
    e = elusion([row(str(i), 0, "discard") for i in range(20)])
    assert (e.k, e.n, e.point, e.low) == (0, 20, 0.0, 0.0)
    assert e.high == pytest.approx(0.1684, abs=1e-4)


def test_empty_piles_raise_naming_pile():
    with pytest.raises(ValueError, match="flagged"):
        precision([])
    with pytest.raises(ValueError, match="discard"):
        elusion([])


def est(point: float, low: float | None = None, high: float | None = None) -> Estimate:
    return Estimate(point, point if low is None else low, point if high is None else high, 1, 2)


def test_richness_arithmetic():
    r = richness(est(0.5, 0.4, 0.6), est(0.05, 0.02, 0.08), 100, 900)
    assert r.point == pytest.approx(0.095)
    assert r.low == pytest.approx((40 + 18) / 1000)
    assert r.high == pytest.approx((60 + 72) / 1000)
    assert (r.k, r.n) == (-1, 1000)


def test_recall_arithmetic():
    r = recall(est(0.5, 0.4, 0.6), est(0.05, 0.02, 0.08), 100, 900)
    assert r.point == pytest.approx(50 / 95)
    assert r.low == pytest.approx(40 / (40 + 72))
    assert r.high == pytest.approx(60 / (60 + 18))
    assert (r.k, r.n) == (-1, 1000)


def test_recall_is_one_when_elusion_zero():
    r = recall(est(0.5, 0.4, 0.6), est(0.0, 0.0, 0.1), 100, 900)
    assert r.point == 1.0
    assert r.high == 1.0


def test_recall_undefined_when_nothing_relevant():
    r = recall(est(0.0, 0.0, 0.0), est(0.0, 0.0, 0.0), 100, 900)
    assert (r.point, r.low, r.high) == (0.0, 0.0, 1.0)


def test_agreement_ignores_non_shared_ids():
    human = [
        row("a", 1, labeler="human"),
        row("b", 0, labeler="human"),
        row("x", 1, labeler="human"),
    ]
    judge = [row("a", 1), row("b", 1), row("y", 0)]
    a = agreement(human, judge)
    assert (a.k, a.n, a.point) == (1, 2, 0.5)


def test_agreement_no_overlap_raises():
    with pytest.raises(ValueError):
        agreement([row("a", 1)], [row("b", 1)])
