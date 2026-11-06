"""The grade flow with a fake grader, fake verdicts and a temp CSV: no Postgres, no API."""

import json

import pytest
from prefect.testing.utilities import prefect_test_harness

from elusion.evaluate.grades import grade_cost_usd, grade_runs_path, read_rows
from elusion.review.prompt import Prompt
from elusion.review.providers import Grading, ReviewError, Verdict, grader_message
from tests.fakes import FakeGrader


@pytest.fixture(scope="module", autouse=True)
def prefect_api():
    with prefect_test_harness():
        yield


@pytest.fixture
def env(monkeypatch, tmp_path):
    import elusion.flows.grade as mod

    texts = {f"d{i:03d}": f"paragraph {i} about hosting" for i in range(40)}
    monkeypatch.setattr(mod, "load_texts", lambda corpus_dir: dict(texts))
    monkeypatch.setattr(
        mod, "load_prompt", lambda c, v: Prompt(f"grade system for {c}", f"{c}/{v}")
    )
    return tmp_path / "grades" / "c.csv", texts


def verdicts_of(n: int) -> dict[str, Verdict]:
    return {
        f"d{i:03d}": Verdict(
            score=3, rationale=f"why {i}", quote=f"paragraph {i}" if i % 2 == 0 else "invented"
        )
        for i in range(n)
    }


def run(path, verdicts, grader, review_model="rev", **kw):
    from elusion.flows.grade import grade

    return grade(
        "c", "v2", review_model=review_model, verdicts=verdicts, grader=grader,
        grades_csv=path, **kw,
    )  # fmt: skip


def test_grades_every_flagged_verdict_and_writes_rows(env) -> None:
    path, texts = env
    g = FakeGrader(sound=lambda t: t.startswith("paragraph 0 "))
    s = run(path, verdicts_of(4), g)
    assert (s.graded, s.skipped, s.failed) == (4, 0, 0)
    assert s.cost_projected_usd is None

    rows = read_rows(path)
    assert [r.doc_id for r in rows] == ["d000", "d001", "d002", "d003"]
    assert [r.sound for r in rows] == [1, 0, 0, 0]
    assert [r.quote_verbatim for r in rows] == [1, 0, 1, 0]  # computed, not asked
    assert {(r.review_model, r.prompt_version, r.score) for r in rows} == {("rev", "c/v2", 3)}
    assert {(r.grader, r.grader_prompt_version) for r in rows} == {("fake-grader", "c/grade-v1")}
    assert all(r.rationale == "canned" and r.graded_at for r in rows)

    # The grader saw the grader prompt, the paragraph and the reviewer's verdict only.
    v = verdicts_of(4)
    assert sorted((sy, p, str(vd)) for sy, p, vd in g.calls) == sorted(
        ("grade system for c", texts[d], str(v[d])) for d in v
    )


def test_grader_never_sees_a_label_or_the_gold_file(env, monkeypatch) -> None:
    import elusion.evaluate.gold as gold

    def boom(*a, **k):
        raise AssertionError("the gold CSV must not be read by grade")

    monkeypatch.setattr(gold, "read_rows", boom)
    monkeypatch.setattr(gold, "judge_rows", boom)
    path, _ = env
    g = FakeGrader()
    verdicts = verdicts_of(3)
    run(path, verdicts, g)
    assert len(g.calls) == 3
    for system, paragraph, verdict in g.calls:
        user = grader_message(paragraph, verdict)
        assert "label" not in user.lower() and "label" not in system.lower()
        assert verdict in verdicts.values()


def test_rerun_skips_graded_keys(env) -> None:
    path, _ = env
    run(path, verdicts_of(3), FakeGrader())
    g = FakeGrader()
    s = run(path, verdicts_of(5), g)
    assert (s.graded, s.skipped) == (2, 3) and len(g.calls) == 2
    # A different reviewer or grader model is a different key: nothing is skipped.
    s = run(path, verdicts_of(5), FakeGrader(), review_model="other")
    assert (s.graded, s.skipped) == (5, 0)
    s = run(path, verdicts_of(5), FakeGrader(model="g2"))
    assert (s.graded, s.skipped) == (5, 0)


class Flaky(FakeGrader):
    def __init__(self, bad: set[str]) -> None:
        super().__init__(model="claude-opus-5-5")
        self.bad = bad

    def grade(self, system, paragraph, verdict):
        if paragraph in self.bad:
            self.calls.append((system, paragraph, verdict))
            raise ReviewError("refusal", self.model, 100_000, 0)
        return super().grade(system, paragraph, verdict)


def test_failed_calls_write_no_row_and_are_retried(env) -> None:
    path, texts = env
    s = run(path, verdicts_of(5), Flaky({texts["d002"]}))
    assert (s.graded, s.failed) == (4, 1)
    assert s.cost_usd == pytest.approx(0.40)
    assert "d002" not in {r.doc_id for r in read_rows(path)}

    g = Flaky(set())
    s = run(path, verdicts_of(5), g)
    assert (s.graded, s.skipped, s.failed) == (1, 4, 0)
    assert [p for _, p, _ in g.calls] == [texts["d002"]]


class EchoMismatch(FakeGrader):
    def grade(self, system, paragraph, verdict) -> Grading:
        out = super().grade(system, paragraph, verdict)
        out.model = "fake-grader-20990101"
        return out


def test_model_echo_mismatch_aborts_and_writes_nothing(env) -> None:
    path, _ = env
    with pytest.raises(RuntimeError, match="fake-grader-20990101"):
        run(path, verdicts_of(5), EchoMismatch())
    assert read_rows(path) == []


class Costly(FakeGrader):
    def grade(self, system, paragraph, verdict) -> Grading:
        out = super().grade(system, paragraph, verdict)
        out.cost_usd = 0.01
        return out


def test_budget_exceeded_stops_after_probe(env) -> None:
    from elusion.flows.review import BudgetExceeded

    path, _ = env
    g = Costly()
    with pytest.raises(BudgetExceeded, match="refusing to continue"):
        run(path, verdicts_of(30), g, max_run_usd=0.05)
    assert len(g.calls) == 20 and len(read_rows(path)) == 20
    (rec,) = [json.loads(ln) for ln in grade_runs_path(path).read_text().splitlines()]
    assert rec["graded"] == 20 and rec["cost_usd"] == pytest.approx(0.20)
    assert rec["model"] == "fake-grader" and rec["finished_at"]


def test_budget_ok_reports_projection_and_ledger_cost(env) -> None:
    path, _ = env
    s = run(path, verdicts_of(30), Costly(), max_run_usd=1.0)
    assert s.graded == 30 and s.cost_usd == pytest.approx(0.30)
    assert s.cost_projected_usd == pytest.approx(0.30)
    assert grade_cost_usd(grade_runs_path(path)) == pytest.approx(0.30)


def test_unknown_id_fails_before_any_call(env) -> None:
    path, _ = env
    g = FakeGrader()
    with pytest.raises(KeyError, match="not in the Parquet corpus"):
        run(path, {"nope": Verdict(score=3, rationale="r", quote="")}, g)
    assert g.calls == []


def test_defaults_read_verdicts_for_the_pair(env, monkeypatch) -> None:
    import elusion.flows.grade as mod

    seen = {}
    monkeypatch.setattr(mod.db, "make_engine", lambda url: "engine")

    def fake_verdicts(engine, model, version, min_score):
        seen.update(args=(engine, model, version, min_score))
        return verdicts_of(2)

    monkeypatch.setattr(mod, "verdicts_for", fake_verdicts)
    path, _ = env
    s = mod.grade(
        "c", "v2", review_model="other", grader=FakeGrader(), grades_csv=path, threshold=4
    )
    assert seen["args"] == ("engine", "other", "c/v2", 4) and s.graded == 2


def test_no_verdicts_is_an_error(env, monkeypatch) -> None:
    import elusion.flows.grade as mod

    monkeypatch.setattr(mod.db, "make_engine", lambda url: "engine")
    monkeypatch.setattr(mod, "verdicts_for", lambda *a: {})
    path, _ = env
    with pytest.raises(RuntimeError, match=r"no flagged scores for \(rev, c/v2\)"):
        mod.grade("c", "v2", review_model="rev", grader=FakeGrader(), grades_csv=path)
