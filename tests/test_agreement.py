"""The owner-against-judge check: counted from the gold CSV alone, with no model call."""

import pytest
from typer.testing import CliRunner

from elusion.cli import app
from elusion.evaluate.agreement import compare, render
from elusion.evaluate.gold import HUMAN, GoldRow, append_rows

JUDGE = "fake-judge"


def row(doc_id: str, label: int, labeler: str, pile: str = "flagged", why: str = "") -> GoldRow:
    return GoldRow(doc_id, label, pile, labeler, "" if labeler == HUMAN else "c/judge-v1", why, "t")


@pytest.fixture
def csv(tmp_path):
    path = tmp_path / "gold" / "c.csv"
    append_rows(
        path,
        [
            row("a", 1, HUMAN), row("b", 1, HUMAN), row("c", 0, HUMAN, "discard"),
            row("d", 0, HUMAN, "discard"), row("e", 1, HUMAN),  # e: the judge has not read it
            row("a", 1, JUDGE), row("b", 0, JUDGE, why="merely mentions oversight"),
            row("c", 0, JUDGE, "discard"), row("d", 1, JUDGE, "discard", "names an audit log"),
            row("z", 1, JUDGE),  # z: the owner has not read it
            row("a", 0, "other-judge"),  # another judge model: not counted
        ],
    )  # fmt: skip
    return path


def test_compare_counts_the_abstracts_both_have_labelled(csv) -> None:
    c = compare(csv, JUDGE, "c/judge-v1")
    assert (c.estimate.k, c.estimate.n) == (2, 4)
    assert c.human_only == 1
    assert [(d.doc_id, d.pile, d.owner, d.judge) for d in c.disagreements] == [
        ("b", "flagged", 1, 0),
        ("d", "discard", 0, 1),
    ]
    assert c.disagreements[0].rationale == "merely mentions oversight"


def test_compare_without_a_shared_abstract_is_an_error(tmp_path) -> None:
    path = tmp_path / "c.csv"
    append_rows(path, [row("a", 1, HUMAN)])
    with pytest.raises(ValueError, match="no doc_ids labeled by both"):
        compare(path, JUDGE, "c/judge-v1")


def test_render_gives_the_count_the_interval_and_each_disagreement(csv) -> None:
    out = render(
        compare(csv, JUDGE, "c/judge-v1"), JUDGE, texts={"b": "Text of b.", "d": "Text of d."}
    )
    assert "agreement: 2 of 4 (0.500), 95% interval [0.068, 0.932]" in out
    assert "1 of the owner's labels has no fake-judge label yet and is not counted" in out
    assert "b (flagged): owner yes, judge no" in out
    assert "judge: merely mentions oversight" in out
    assert "Text of b." in out and "Text of d." in out
    assert "d (discard): owner no, judge yes" in out


def test_render_when_they_agree_on_everything(tmp_path) -> None:
    path = tmp_path / "c.csv"
    append_rows(path, [row("a", 1, HUMAN), row("a", 1, JUDGE)])
    out = render(compare(path, JUDGE, "c/judge-v1"), JUDGE, texts={})
    assert "agreement: 1 of 1" in out and "No disagreement." in out


def test_cli_agreement_prints_the_comparison(monkeypatch, tmp_path, csv) -> None:
    import dataclasses

    import elusion.config as config
    import elusion.review.corpus as corpus_mod

    monkeypatch.setattr(
        config,
        "settings",
        dataclasses.replace(config.settings, data_dir=tmp_path, judge_model=JUDGE),
    )
    monkeypatch.setattr(corpus_mod, "load_texts", lambda d: {"b": "Text of b.", "d": "Text of d."})
    crit = tmp_path / "c.md"
    crit.write_text("x")
    r = CliRunner().invoke(app, ["agreement", "--criterion", str(crit)])
    assert r.exit_code == 0, r.output
    assert "agreement: 2 of 4" in r.output and "Text of b." in r.output


def test_cli_agreement_with_nothing_to_compare_exits_1(monkeypatch, tmp_path) -> None:
    import dataclasses

    import elusion.config as config

    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, data_dir=tmp_path))
    crit = tmp_path / "c.md"
    crit.write_text("x")
    r = CliRunner().invoke(app, ["agreement", "--criterion", str(crit)])
    assert r.exit_code == 1
    assert "error: no doc_ids labeled by both" in r.stderr


def test_compare_reads_only_the_named_judge_prompts_rows(tmp_path) -> None:
    from elusion.evaluate.gold import GoldRow, append_rows

    p = tmp_path / "g.csv"
    append_rows(
        p,
        [
            GoldRow("a", 1, "flagged", HUMAN, "", "", "t"),
            GoldRow("a", 0, "flagged", JUDGE, "c/judge-v1", "old", "t"),
            GoldRow("a", 1, "flagged", JUDGE, "c/judge-v2", "new", "t"),
        ],
    )
    assert compare(p, JUDGE, "c/judge-v1").estimate.k == 0
    assert compare(p, JUDGE, "c/judge-v2").estimate.k == 1


def test_a_revised_label_replaces_the_blind_one_and_both_counts_are_shown(tmp_path) -> None:
    from elusion.evaluate.gold import REVISED, GoldRow, append_rows, human_rows

    p = tmp_path / "g.csv"
    append_rows(
        p,
        [
            GoldRow("a", 0, "flagged", HUMAN, "", "", "t"),
            GoldRow("b", 1, "discard", HUMAN, "", "", "t"),
            GoldRow("a", 1, "flagged", JUDGE, "c/judge-v1", "why", "t"),
            GoldRow("b", 1, "discard", JUDGE, "c/judge-v1", "why", "t"),
            GoldRow("a", 1, "flagged", HUMAN, REVISED, "could not read it", "t2"),
        ],
    )
    assert [(r.doc_id, r.label) for r in human_rows(p)] == [("a", 1), ("b", 1)]
    assert [(r.doc_id, r.label) for r in human_rows(p, blind=True)] == [("a", 0), ("b", 1)]
    c = compare(p, JUDGE, "c/judge-v1")
    assert (c.estimate.k, c.blind.k, c.revised) == (2, 1, 1)
    out = render(c, JUDGE, texts={})
    assert "agreement: 2 of 2" in out
    assert "blind labels, before he revised 1 after reading the judge: 1 of 2" in out
