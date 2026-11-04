from pathlib import Path

from typer.testing import CliRunner

from elusion.cli import app
from elusion.evaluate.gold import GoldRow, append_rows, human_rows
from elusion.evaluate.label import run_session, select_for_audit

JUDGE = "claude-opus-x"


def _judge(doc_id: str, pile: str) -> GoldRow:
    return GoldRow(doc_id, 1 if pile == "flagged" else 0, pile, JUDGE, "v2", "why", "t")


def _seed_gold(path: Path, flagged: int = 10, discard: int = 10) -> None:
    rows = [_judge(f"f{i:02d}", "flagged") for i in range(flagged)]
    rows += [_judge(f"d{i:02d}", "discard") for i in range(discard)]
    append_rows(path, rows)


def _feed(answers):
    it = iter(answers)
    return lambda: next(it)


def test_selection_balances_piles_and_is_seeded(tmp_path):
    p = tmp_path / "g.csv"
    _seed_gold(p)
    sel = select_for_audit(p, n=6, seed=1)
    assert sum(1 for d, _ in sel if d.startswith("f")) == 3
    assert sum(1 for d, _ in sel if d.startswith("d")) == 3
    assert sel == select_for_audit(p, n=6, seed=1)


def test_selection_excludes_already_labeled_and_takes_all_of_small_pile(tmp_path):
    p = tmp_path / "g.csv"
    _seed_gold(p, flagged=2, discard=10)
    append_rows(p, [GoldRow("d00", 0, "discard", "human", "", "", "t")])
    sel = select_for_audit(p, n=10, seed=1)
    ids = [d for d, _ in sel]
    assert "d00" not in ids
    assert {"f00", "f01"} <= set(ids)
    assert len(ids) == 2 + 5


def test_session_writes_rows_and_handles_input(tmp_path):
    p = tmp_path / "g.csv"
    _seed_gold(p, 1, 1)
    out: list[str] = []
    texts = {"f00": "FLAGGED TEXT", "d00": "DISCARD TEXT"}
    summary = run_session(
        p,
        texts,
        [("f00", "flagged"), ("d00", "discard")],
        reader=_feed(["x", "", "Y", "n"]),
        writer=out.append,
    )
    rows = {r.doc_id: r for r in human_rows(p)}
    assert rows["f00"].label == 1 and rows["f00"].pile == "flagged"
    assert rows["d00"].label == 0 and rows["d00"].pile == "discard"
    assert rows["f00"].labeler == "human" and rows["f00"].prompt_version == ""
    assert rows["f00"].rationale == ""
    assert summary == {"answered": 2, "skipped": 0, "remaining": 0}
    assert any("1/2" in o for o in out) and any("2/2" in o for o in out)


def test_skip_writes_nothing_and_quit_keeps_answers(tmp_path):
    p = tmp_path / "g.csv"
    _seed_gold(p, 2, 1)
    sel = [("f00", "flagged"), ("f01", "flagged"), ("d00", "discard")]
    texts = {d: f"text {d}" for d, _ in sel}
    summary = run_session(p, texts, sel, reader=_feed(["y", "s", "q"]), writer=lambda s: None)
    assert [r.doc_id for r in human_rows(p)] == ["f00"]
    assert summary == {"answered": 1, "skipped": 1, "remaining": 1}


def test_display_shows_only_text_and_counter(tmp_path):
    p = tmp_path / "g.csv"
    _seed_gold(p, 1, 0)
    out: list[str] = []
    run_session(
        p, {"f00": "just words"}, [("f00", "flagged")], reader=_feed(["n"]), writer=out.append
    )
    shown = "\n".join(out)
    assert "just words" in shown
    for leaked in ("f00", "flagged", "discard", "why", JUDGE, "v2"):
        assert leaked not in shown


def test_cli_label_wires_selection_and_session(tmp_path, monkeypatch):
    import elusion.evaluate.label as mod

    crit = tmp_path / "c.md"
    crit.write_text("x")
    seen = {}
    monkeypatch.setattr(
        mod, "label", lambda criterion, n, seed: seen.update(c=criterion, n=n, s=seed) or "done"
    )
    r = CliRunner().invoke(
        app,
        ["label", "--criterion", str(crit), "--prompt-version", "v2", "--n", "4", "--seed", "3"],
    )
    assert r.exit_code == 0 and "done" in r.output
    assert seen == {"c": "c", "n": 4, "s": 3}


def test_selection_order_does_not_reveal_pile(tmp_path):
    p = tmp_path / "g.csv"
    _seed_gold(p, 20, 20)
    piles = [pile for _, pile in select_for_audit(p, n=20, seed=1)]
    assert piles != ["flagged"] * 10 + ["discard"] * 10
    assert piles != sorted(piles)
    assert piles.count("flagged") == 10


def test_eof_acts_as_quit(tmp_path):
    p = tmp_path / "g.csv"
    _seed_gold(p, 1, 0)

    def boom():
        raise EOFError

    summary = run_session(p, {"f00": "t"}, [("f00", "flagged")], reader=boom, writer=lambda s: None)
    assert summary == {"answered": 0, "skipped": 0, "remaining": 1}


def test_label_rejects_non_positive_n():
    import pytest
    import typer

    from elusion.evaluate.label import label

    for n in (0, -4, 1):
        with pytest.raises(typer.BadParameter, match="need at least 2"):
            label("c", n)


def test_label_rounds_odd_n_down_with_a_note(tmp_path, monkeypatch, capsys):
    import elusion.evaluate.label as mod

    seen = {}

    def fake_select(path, n, seed):
        seen["n"] = n
        return []

    monkeypatch.setattr(mod, "select_for_audit", fake_select)
    mod.label("c", 7, seed=1)
    assert seen["n"] == 6
    assert "rounding n=7 down to 6" in capsys.readouterr().out
