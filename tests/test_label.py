from pathlib import Path

from typer.testing import CliRunner

from elusion.cli import app
from elusion.evaluate.gold import GoldRow, append_rows, human_rows
from elusion.evaluate.label import criterion_summary, run_session, select_for_audit

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
        mod,
        "label",
        lambda criterion, n, seed, **kw: seen.update(c=criterion, n=n, s=seed) or "done",
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

    def fake_select(path, n, seed, *a):
        seen["n"] = n
        return []

    monkeypatch.setattr(mod, "select_for_audit", fake_select)
    mod.label("c", 7, seed=1)
    assert seen["n"] == 6
    assert "rounding n=7 down to 6" in capsys.readouterr().out


def test_label_reads_the_texts_of_the_criterions_corpus(monkeypatch, tmp_path):
    import dataclasses

    import elusion.config as config
    import elusion.evaluate.label as mod
    import elusion.review.corpus as corpus_mod

    seen = []
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, data_dir=tmp_path))
    monkeypatch.setattr(mod, "select_for_audit", lambda path, n, seed, *a: [("f00", "flagged")] * n)
    monkeypatch.setattr(corpus_mod, "load_texts", lambda text_dir: seen.append(text_dir) or {})
    monkeypatch.setattr(
        mod,
        "run_session",
        lambda path, texts, sel, **kw: {"answered": 0, "skipped": 0, "remaining": 0},
    )
    mod.label("cloud-concentration", 2)
    mod.label("auditable-decisions", 2)
    assert seen == [tmp_path / "corpus", tmp_path / "abstracts"]


def _cands(flagged: int = 10, discard: int = 10) -> list[tuple[str, str]]:
    return [(f"f{i:02}", "flagged") for i in range(flagged)] + [
        (f"d{i:02}", "discard") for i in range(discard)
    ]


def test_selection_from_candidates_needs_no_judge_rows(tmp_path):
    p = tmp_path / "g.csv"  # does not exist
    cands = _cands()
    sel = select_for_audit(p, n=6, seed=1, candidates=cands)
    assert sum(1 for _, pile in sel if pile == "flagged") == 3
    assert sum(1 for _, pile in sel if pile == "discard") == 3
    assert sel == select_for_audit(p, n=6, seed=1, candidates=list(reversed(cands)))


def test_selection_from_candidates_ignores_the_judges_rows(tmp_path):
    p = tmp_path / "g.csv"
    append_rows(p, [_judge("zz", "flagged")])  # judged, but not in the draw
    sel = select_for_audit(p, n=20, seed=1, candidates=_cands(2, 2))
    assert {d for d, _ in sel} == {"f00", "f01", "d00", "d01"}


def test_excluded_ids_are_never_selected(tmp_path):
    p = tmp_path / "g.csv"
    cands = [("f00", "flagged"), ("f01", "flagged"), ("d00", "discard"), ("d01", "discard")]
    sel = select_for_audit(p, n=4, seed=1, candidates=cands, exclude={"f00", "d01"})
    assert {d for d, _ in sel} == {"f01", "d00"}


def test_excluded_ids_are_never_selected_from_judge_rows_either(tmp_path):
    p = tmp_path / "g.csv"
    _seed_gold(p, 2, 2)
    sel = select_for_audit(p, n=4, seed=1, exclude={"f00", "d01"})
    assert {d for d, _ in sel} == {"f01", "d00"}


def test_candidates_already_labelled_by_the_owner_are_skipped(tmp_path):
    p = tmp_path / "g.csv"
    append_rows(p, [GoldRow("f00", 1, "flagged", "human", "", "", "t")])
    sel = select_for_audit(p, n=20, seed=1, candidates=_cands(3, 3))
    assert "f00" not in {d for d, _ in sel}
    assert len(sel) == 5


def _label_env(monkeypatch, tmp_path):
    """Real `label`, with the texts, the data dir and the terminal session replaced."""
    import dataclasses

    import elusion.config as config
    import elusion.evaluate.label as mod
    import elusion.review.corpus as corpus_mod

    sessions = []
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, data_dir=tmp_path))
    monkeypatch.setattr(corpus_mod, "load_texts", lambda text_dir: {})
    monkeypatch.setattr(
        mod,
        "run_session",
        lambda path, texts, sel, **kw: (
            sessions.append(sel) or {"answered": 0, "skipped": 0, "remaining": 0}
        ),
    )
    crit = tmp_path / "c.md"
    crit.write_text("x")
    return str(crit), sessions


DRAW_ARGS = ["--prompt-version", "v1", "--year", "2026", "--year", "2025"]
DRAW_ARGS += ["--review-model", "gpt-6-luna", "--flagged-n", "400", "--discard-n", "400"]


def test_cli_label_with_year_draws_from_the_piles(monkeypatch, tmp_path):
    import elusion.flows.evaluate as flows

    crit, sessions = _label_env(monkeypatch, tmp_path)
    seen = {}

    def fake_draw(criterion, prompt_version, **kw):
        seen.update(criterion=criterion, prompt_version=prompt_version, **kw)
        return _cands(3, 3)

    monkeypatch.setattr(flows, "draw_gold", fake_draw)
    args = ["label", "--criterion", crit, *DRAW_ARGS, "--n", "6"]
    r = CliRunner().invoke(app, [*args, "--exclude", "f00", "--exclude", "d02"])
    assert r.exit_code == 0, r.output
    assert (seen["criterion"], seen["prompt_version"]) == ("c", "v1")
    assert (seen["flagged_n"], seen["discard_n"], seen["years"]) == (400, 400, [2025, 2026])
    assert (seen["review_model"], seen["threshold"]) == ("gpt-6-luna", 3)
    (session,) = sessions
    assert {d for d, _ in session} == {"f01", "f02", "d00", "d01"}


def test_cli_label_refuses_while_the_review_runs(monkeypatch, tmp_path):
    import elusion.flows.evaluate as flows

    crit, sessions = _label_env(monkeypatch, tmp_path)

    def refuse(*a, **kw):
        raise flows.ReviewStillRunning("review job(s) job-abc still running")

    monkeypatch.setattr(flows, "draw_gold", refuse)
    r = CliRunner().invoke(app, ["label", "--criterion", crit, *DRAW_ARGS, "--n", "6"])
    assert r.exit_code == 1
    assert "error: review job(s) job-abc still running" in r.stderr
    assert "Traceback" not in r.output
    assert sessions == []


def test_label_from_the_draw_says_why_nothing_is_left(monkeypatch, tmp_path):
    import elusion.evaluate.label as mod

    _label_env(monkeypatch, tmp_path)
    out = mod.label("c", 2, candidates=[("f00", "flagged")], exclude={"f00"})
    assert out == "nothing to label: every drawn abstract is labelled or excluded"


def test_cli_label_force_draws_despite_an_unfinished_review_job(monkeypatch, tmp_path):
    """A killed review job never gets a finished_at; --force is the way past it, as on judge."""
    import elusion.flows.evaluate as flows

    crit, sessions = _label_env(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(flows, "draw_gold", lambda c, v, **kw: seen.update(kw) or _cands(3, 3))
    args = ["label", "--criterion", crit, *DRAW_ARGS, "--n", "6"]
    assert CliRunner().invoke(app, args).exit_code == 0 and seen["force"] is False
    r = CliRunner().invoke(app, [*args, "--force"])
    assert r.exit_code == 0, r.output
    assert seen["force"] is True and len(sessions) == 2


def test_a_second_sitting_resumes_the_same_pick(tmp_path):
    """Quit part-way, run the same command again: the unanswered ones, not a fresh pick."""
    p = tmp_path / "g.csv"
    first = select_for_audit(p, n=6, seed=1, candidates=_cands())
    answered = first[:2]
    append_rows(p, [GoldRow(d, 1, pile, "human", "", "", "t") for d, pile in answered])
    second = select_for_audit(p, n=6, seed=1, candidates=_cands())
    assert len(second) == 4
    assert set(second) == set(first) - set(answered)


def test_a_larger_n_keeps_the_abstracts_a_smaller_n_picked(tmp_path):
    p = tmp_path / "g.csv"
    small = select_for_audit(p, n=6, seed=1, candidates=_cands())
    large = select_for_audit(p, n=10, seed=1, candidates=_cands())
    assert set(small) < set(large) and len(large) == 10


def test_label_says_how_many_of_the_pick_are_left(monkeypatch, tmp_path, capsys):
    import elusion.evaluate.label as mod

    _label_env(monkeypatch, tmp_path)
    first = select_for_audit(tmp_path / "gold" / "c.csv", n=6, seed=1, candidates=_cands())
    append_rows(
        tmp_path / "gold" / "c.csv",
        [GoldRow(d, 1, pile, "human", "", "", "t") for d, pile in first[:2]],
    )
    mod.label("c", 6, seed=1, candidates=_cands())
    assert "note: 4 of the 6 picked are still to label" in capsys.readouterr().out
    append_rows(
        tmp_path / "gold" / "c.csv",
        [GoldRow(d, 1, pile, "human", "", "", "t") for d, pile in first[2:]],
    )
    out = mod.label("c", 6, seed=1, candidates=_cands())
    assert out == "nothing to label: all 6 picked are labelled; raise --n to label more"


def test_cli_label_seed_reaches_the_draw(monkeypatch, tmp_path):
    import elusion.flows.evaluate as flows

    crit, _ = _label_env(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(flows, "draw_gold", lambda c, v, **kw: seen.update(kw) or _cands(3, 3))
    args = ["label", "--criterion", crit, *DRAW_ARGS, "--n", "6", "--seed", "11"]
    assert CliRunner().invoke(app, args).exit_code == 0
    assert seen["seed"] == 11


def test_criterion_summary_is_the_quoted_paragraph_only(tmp_path):
    crit = tmp_path / "c.md"
    crit.write_text(
        "# T\n\n> Studies how a thing\n> is checked.\n\nIt counts when:\n- a list item\n"
    )
    assert criterion_summary(crit) == "Studies how a thing is checked."


def test_session_asks_the_question_in_words_under_each_text(tmp_path):
    p = tmp_path / "g.csv"
    _seed_gold(p, 2, 0)
    out: list[str] = []
    run_session(
        p,
        {"f00": "one", "f01": "two"},
        [("f00", "flagged"), ("f01", "flagged")],
        reader=_feed(["y", "n"]),
        writer=out.append,
        topic="Studies how a thing is checked.",
    )
    shown = "\n".join(out)
    assert shown.count("Topic: Studies how a thing is checked.") == 2
    assert shown.count("Is this text about that topic?") == 2


def test_cli_label_passes_the_criterions_summary_as_the_topic(tmp_path, monkeypatch):
    import elusion.evaluate.label as mod

    crit = tmp_path / "c.md"
    crit.write_text("# T\n\n> About a thing.\n")
    seen = {}
    monkeypatch.setattr(mod, "label", lambda criterion, n, seed, **kw: seen.update(kw) or "done")
    r = CliRunner().invoke(app, ["label", "--criterion", str(crit), "--prompt-version", "v2"])
    assert r.exit_code == 0 and seen["topic"] == "About a thing."
