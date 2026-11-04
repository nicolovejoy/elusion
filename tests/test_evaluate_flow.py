"""The evaluate flow with a fake engine (stubbed readers), a temp gold CSV and a temp
reports directory: no Postgres, no API."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from prefect.testing.utilities import prefect_test_harness

from elusion.evaluate.gold import HUMAN, GoldRow, append_judge_run, append_rows, gold_path

JUDGE = "fake-judge"
# A real Engine object (Prefect validates flow parameter types) that is never queried:
# every reader that would touch Postgres is replaced in the `env` fixture.
FAKE_ENGINE = sa.create_engine("sqlite://")
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module", autouse=True)
def prefect_api():
    with prefect_test_harness():
        yield


def row(doc_id: str, label: int, labeler: str = JUDGE, pile: str = "flagged") -> GoldRow:
    pv = "" if labeler == HUMAN else "c/judge-v1"
    return GoldRow(doc_id, label, pile, labeler, pv, "r", "2026-11-04T00:00:00+00:00")


# f00-f03 flagged (score 3), g00-g15 discard (score 0); 20 docs in the population.
SCORES = {**{f"f{i:02d}": 3 for i in range(4)}, **{f"g{i:02d}": 0 for i in range(16)}}


@pytest.fixture
def env(monkeypatch, tmp_path):
    import elusion.flows.evaluate as mod

    seen: dict = {}

    def fake_scores_for(engine, model, prompt_version):
        seen["scores_for"] = (engine, model, prompt_version)
        return dict(SCORES)

    def fake_jobs_summary(engine, model, prompt_version):
        seen["jobs_summary"] = (engine, model, prompt_version)
        return {
            "jobs": 2,
            "cost_usd": 0.75,
            "started_at": T0,
            "finished_at": T0 + timedelta(hours=1, minutes=2, seconds=3),
            "unfinished": False,
            "unfinished_ids": [],
        }

    def fake_company_counts(engine, doc_ids):
        seen["company_ids"] = sorted(doc_ids)
        return [("Acme Corp", len(seen["company_ids"]))]

    monkeypatch.setattr(mod, "scores_for", fake_scores_for)
    monkeypatch.setattr(mod, "jobs_summary", fake_jobs_summary)
    monkeypatch.setattr(mod, "company_counts", fake_company_counts)

    csv = gold_path("c", tmp_path)
    append_rows(
        csv,
        [row("f00", 1), row("f01", 1), row("f02", 1), row("f03", 0)]
        + [row(f"g{i:02d}", 1 if i == 0 else 0, pile="discard") for i in range(10)]
        # another judge model's row: not this judge, ignored
        + [row("g11", 1, labeler="other-judge", pile="discard")]
        + [row("f00", 1, labeler=HUMAN), row("g00", 1, labeler=HUMAN)],
    )
    append_judge_run(csv.with_name("c.judge-runs.jsonl"), {"cost_usd": 0.5})
    append_judge_run(csv.with_name("c.judge-runs.jsonl"), {"cost_usd": 0.25})
    return tmp_path, seen


def run(tmp_path, **kw):
    from elusion.flows.evaluate import evaluate

    args = dict(
        prompt_version="v2",
        engine=FAKE_ENGINE,
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        review_model="rev-model",
        judge_model=JUDGE,
        today="2026-11-04",
    )
    return evaluate("c", **{**args, **kw})


def test_writes_the_report_and_returns_its_path(env) -> None:
    tmp_path, seen = env
    path = run(tmp_path, seed=7)
    assert path == tmp_path / "reports" / "c-rev-model-v2.md"
    md = path.read_text()

    assert seen["scores_for"] == (FAKE_ENGINE, "rev-model", "c/v2")
    assert seen["jobs_summary"] == (FAKE_ENGINE, "rev-model", "c/v2")
    assert seen["company_ids"] == ["f00", "f01", "f02"]  # flagged and judged relevant

    assert "- population N: 20" in md
    assert "- flagged (score >= 3): 4" in md
    assert "- discard (score < 3): 16" in md
    assert "- discard sample labeled by the judge: 10" in md
    assert "(k/n = 3/4)" in md  # precision
    assert "(k/n = 1/10)" in md  # elusion; the other judge's g11 row is not counted
    assert "- agreement: 1.000" in md and "(k/n = 2/2)" in md
    assert "- judge model: fake-judge" in md
    assert "- judge prompt version: c/judge-v1" in md
    assert "- seed: 7" in md and "- date: 2026-11-04" in md
    assert "$0.7500 (sum over 2 review_jobs rows)" in md
    assert "- judge run: $0.7500" in md
    assert "across 2 review_jobs rows: 1:02:03" in md
    assert "- Acme Corp: 3" in md
    assert "-1/" not in md and "corpus" not in md


def test_piles_come_from_scores_not_the_csv_pile_column(env, monkeypatch) -> None:
    import elusion.flows.evaluate as mod

    tmp_path, _ = env
    # g00 now scores 3: it moves into the flagged pile (its judge label is 1) although
    # the CSV still says "discard"; f03 drops to 2 and is now a discard-sample row.
    rescored = {**SCORES, "g00": 3, "f03": 2}
    monkeypatch.setattr(mod, "scores_for", lambda e, m, p: dict(rescored))
    md = run(tmp_path).read_text()
    assert "- flagged (score >= 3): 4" in md
    assert "(k/n = 4/4)" in md  # f00 f01 f02 g00
    assert "(k/n = 0/10)" in md  # f03 + g01..g09


def test_refuses_when_flagged_docs_lack_a_judge_label(env, monkeypatch) -> None:
    import elusion.flows.evaluate as mod

    tmp_path, _ = env
    monkeypatch.setattr(mod, "scores_for", lambda e, m, p: {**SCORES, "x01": 4, "x02": 3, "x03": 2})
    with pytest.raises(mod.MissingLabels, match=r"2 flagged .*elusion judge"):
        run(tmp_path)
    assert not (tmp_path / "reports").exists()


def test_no_scores_is_an_error(env, monkeypatch) -> None:
    import elusion.flows.evaluate as mod

    tmp_path, _ = env
    monkeypatch.setattr(mod, "scores_for", lambda e, m, p: {})
    with pytest.raises(RuntimeError, match=r"no scores for \(rev-model, c/v2\)"):
        run(tmp_path)


def test_no_human_rows_means_no_agreement(env) -> None:
    tmp_path, _ = env
    csv = gold_path("c", tmp_path)
    lines = [ln for ln in csv.read_text().splitlines(keepends=True) if f",{HUMAN}," not in ln]
    csv.write_text("".join(lines))
    md = run(tmp_path).read_text()
    assert "No human audit labels" in md


def test_warns_but_writes_the_report_while_a_review_job_is_unfinished(
    env, monkeypatch, capsys
) -> None:
    import elusion.flows.evaluate as mod

    tmp_path, _ = env
    monkeypatch.setattr(
        mod,
        "jobs_summary",
        lambda e, m, p: {
            "jobs": 1,
            "cost_usd": 0.1,
            "started_at": T0,
            "finished_at": None,
            "unfinished": True,
            "unfinished_ids": ["job-abc"],
        },
    )
    path = run(tmp_path)
    assert path.is_file()
    err = capsys.readouterr().err
    assert "WARNING" in err and "job-abc" in err and "still running" in err


def test_no_warning_when_every_review_job_finished(env, capsys) -> None:
    tmp_path, _ = env
    run(tmp_path)
    assert "still running" not in capsys.readouterr().err
