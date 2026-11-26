# Phase 7 Judge Sample Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The judge, the grader and the report work from a seeded random sample of the flagged pile, and the owner can label 40 abstracts blind before the judge has labelled anything.

**Architecture:** One draw decides everything. `split_piles` gains a limit on the flagged pile; `judge`, `label`, `grade` and `evaluate` each recompute the same draw from the scores and the seed, as `judge` and `evaluate` already do for the discard pile. Nothing stores the draw. With no limit given, every function behaves exactly as it does today.

**Tech Stack:** Python 3.12, `uv`, `pytest`, `ruff`, Prefect 3 flows, Typer CLI, SQLAlchemy. No new dependency.

**Status:** approved by the owner on 2026-11-20; built the same day, one session for every task, then one fresh reviewer for the branch. Every publication year in the corpus is 2025 or 2026 (checked 2026-11-20), so `--year 2025 --year 2026` names the whole population.

**Spec:** `docs/plans/phase-7-literature.md`, task 5, the block "The judge pass, 2026-11-20". Decisions: `docs/DECISIONS.md`, the two lines dated 2026-11-20.

## Global Constraints

- The gold set (`data/gold/`) is never in any prompt, few-shot example or retrieval context.
- Idempotent by document id: no re-run pays the judge or the grader twice for the same document.
- Every model call carries a pinned model id and a prompt version, both stored with the result. This plan adds no new call site.
- The 10-K reports do not change: with `flagged_n` left out, the draw, the labels asked for and the rendered report are byte-for-byte what they are today.
- Tests make no paid call and need no network: use the fakes in `tests/fakes.py` and the patterns of the existing flow tests.
- `uv run ruff check` and `uv run ruff format --check` pass after every task.
- The sizes for `auditable-decisions`: 400 flagged, 400 discard, 40 owner labels (20 from each pile), seed `DEFAULT_SEED` (20261104), threshold 3.
- The six abstracts the owner judged on 2026-11-19 are never shown to him by `label`: `b5a579121f7c0f6a`, `4839dd2f05549e59`, `11e01572681982b3`, `7e84529379fbbf14`, `cb52fb2ee7bc1a71`, `c70aa5cfcdacaff1`.

## Out of scope

- Rewriting the judge prompt (`judge-v2`). It is planned only if step 5 of the spec gives B. It will need two things this plan does not build: `JUDGE_PROMPT_VERSION` is a constant, and gold rows are keyed by (doc_id, labeler) with no prompt version, so a second judge prompt under the same model could not relabel the first 40.
- Harvest recall, the PRISMA counts and "works kept" in the report (task 6 of the spec). This plan only makes sure the flagged seeds have judge labels.

## Review Focus

1. A limit on the flagged pile must not move the discard draw. If it did, the existing `cloud-concentration` gold would no longer match its draw and `evaluate` would raise `MissingDiscardLabels`. Test in Task 1.
2. `judge --only-labeled` when the owner has labelled nothing: an error that says so, not a run that silently labels zero. Test in Task 2.
3. `label` drawing from the piles while a review job is unfinished: refused, because the draw depends on the whole pile and he would label abstracts that fall out of the final sample. Test in Task 3.
4. `evaluate` run without the `--flagged-n` the judge was given: the error names the flag. And recall and richness are still weighted by the size of the whole flagged pile, not by the sample. Tests in Task 6.
5. A flagged seed that is also in the random 400 is judged once and counts in precision; a flagged seed outside the 400 is judged and never counts in precision. Tests in Tasks 4 and 6.

---

### Task 1: A limit on the flagged pile

**Files:**
- Modify: `src/elusion/evaluate/gold.py` (`split_piles`, `select_gold`, module docstring)
- Test: `tests/test_gold.py`

**Interfaces:**
- Produces: `split_piles(scores: Mapping[str, int], threshold: int, discard_n: int, seed: int, flagged_n: int | None = None) -> list[tuple[str, Pile]]` and `select_gold(engine, model, prompt_version, threshold=3, discard_n=200, seed=DEFAULT_SEED, years=None, flagged_n: int | None = None)`.

- [ ] **Step 1: Write the failing tests**

```python
SCORES = {f"f{i:03}": 4 for i in range(50)} | {f"d{i:03}": 1 for i in range(100)}


def test_flagged_limit_samples_the_flagged_pile():
    got = split_piles(SCORES, 3, 10, seed=7, flagged_n=20)
    flagged = [d for d, p in got if p == "flagged"]
    assert len(flagged) == 20 and len(set(flagged)) == 20
    assert all(d.startswith("f") for d in flagged)
    assert got == split_piles(dict(reversed(SCORES.items())), 3, 10, seed=7, flagged_n=20)
    assert flagged != sorted(SCORES)[:20]  # a draw, not the first twenty


def test_flagged_limit_does_not_move_the_discard_draw():
    def discards(**kw):
        return [d for d, p in split_piles(SCORES, 3, 10, seed=7, **kw) if p == "discard"]

    assert discards() == discards(flagged_n=20) == discards(flagged_n=5)


def test_no_flagged_limit_is_todays_result():
    got = split_piles(SCORES, 3, 10, seed=7)
    assert [d for d, p in got if p == "flagged"] == sorted(d for d in SCORES if d.startswith("f"))


def test_flagged_pile_smaller_than_the_limit_is_taken_whole():
    got = split_piles(SCORES, 3, 10, seed=7, flagged_n=500)
    assert sum(1 for _, p in got if p == "flagged") == 50
```

Add to the existing `test_select_gold_reads_scores_for_the_pair` pattern one test, `test_select_gold_passes_the_flagged_limit`, asserting that `select_gold(..., flagged_n=1)` returns exactly one flagged id.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_gold.py -q`
Expected: the new tests fail with `TypeError: split_piles() got an unexpected keyword argument 'flagged_n'`.

- [ ] **Step 3: Implement**

The flagged sample uses its own `random.Random(seed)`, separate from the discard pile's, so that neither draw consumes the other's random numbers. The sampled flagged ids are returned sorted. `flagged_n=None` takes the whole pile. Update the module docstring's sentence "every paragraph the review flagged" to say "or a seeded random sample of them when a limit is given".

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_gold.py -q`
Expected: all pass.

- [ ] **Step 5: Commit** (`Phase 7 task 5: split_piles can sample the flagged pile`)

---

### Task 2: The judge reads the sample, or only what the owner has labelled

**Files:**
- Modify: `src/elusion/flows/evaluate.py` (`judge`, new `draw_gold`)
- Modify: `src/elusion/cli.py` (`judge` command)
- Test: `tests/test_judge_flow.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `select_gold(..., flagged_n=...)` from Task 1.
- Produces:
  - `draw_gold(criterion: str, prompt_version: str, *, years: list[int], threshold: int = 3, discard_n: int = 200, seed: int = DEFAULT_SEED, review_model: str | None = None, flagged_n: int | None = None, force: bool = False, engine: Engine | None = None) -> list[tuple[str, Pile]]`. It is the body of `judge`'s `if gold is None:` block, moved out unchanged in behaviour: it raises `ReviewStillRunning` for an unfinished review job unless `force`, and `RuntimeError` when the pair has no scores. It is a plain function, not a Prefect task; it warns with `logging.getLogger(__name__)`.
  - `judge(..., flagged_n: int | None = None, only_human: bool = False)`.
  - CLI: `elusion judge --flagged-n INT --only-labeled`.

- [ ] **Step 1: Write the failing tests** in `tests/test_judge_flow.py`, in the style of the existing tests there (fake provider, `gold=` and `gold_csv=` passed in):

```python
def test_only_human_labels_just_the_ids_the_owner_labelled(tmp_path):
    # gold = 3 flagged + 3 discard; the CSV holds HUMAN rows for one of each
    # after judge(..., gold=gold, gold_csv=csv, only_human=True):
    assert summary.labeled == 2
    assert labeled_ids(csv, provider.model) == {"f1", "d1"}


def test_only_human_with_no_human_labels_is_an_error(tmp_path):
    with pytest.raises(RuntimeError, match="no human labels"):
        judge(..., gold=gold, gold_csv=csv, only_human=True)


def test_full_run_after_only_human_does_not_pay_twice(tmp_path):
    # run once with only_human=True, then again without it
    assert second.skipped == 2 and second.labeled == 4
```

And two for `draw_gold`, with the fake engine the existing `ReviewStillRunning` test uses: `test_draw_gold_refuses_an_unfinished_review` (raises `ReviewStillRunning`; passes with `force=True`) and `test_draw_gold_passes_the_flagged_limit` (with `flagged_n=1`, one flagged id). In `tests/test_cli.py`: `test_judge_cli_passes_flagged_n_and_only_labeled`, asserting the flow is called with `flagged_n=400, only_human=True` for `--flagged-n 400 --only-labeled`.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_judge_flow.py tests/test_cli.py -q`
Expected: the new tests fail on the unknown keyword or the missing `draw_gold`.

- [ ] **Step 3: Implement**

`judge` calls `draw_gold` when `gold` is None. With `only_human`, the gold is narrowed to the ids in `labeled_ids(path, HUMAN)` before `todo` is computed; an empty result raises `RuntimeError("no human labels in <path> among the drawn gold ids; run `elusion label` first")`. The existing `ReviewStillRunning` tests must still pass unchanged.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_judge_flow.py tests/test_cli.py -q`
Expected: all pass.

- [ ] **Step 5: Commit** (`Phase 7 task 5: judge takes a flagged limit and --only-labeled`)

---

### Task 3: The owner labels from the draw, before the judge

**Files:**
- Modify: `src/elusion/evaluate/label.py` (`select_for_audit`, `label`)
- Modify: `src/elusion/cli.py` (`label` command)
- Test: `tests/test_label.py`

**Interfaces:**
- Consumes: `draw_gold` from Task 2.
- Produces:
  - `select_for_audit(path: Path, n: int, seed: int, candidates: Sequence[tuple[str, str]] | None = None, exclude: Collection[str] = ()) -> list[tuple[str, str]]`. With `candidates` (the drawn gold ids with their piles) it picks from them and never reads the judge's rows; without, it picks from the judge's rows as today. `exclude` removes ids either way.
  - `label(criterion: str, n: int, seed: int = DEFAULT_SEED, *, candidates=None, exclude=()) -> str`.
  - CLI: `elusion label --criterion PATH --prompt-version v1 --year 2025 --year 2026 --review-model gpt-6-luna --flagged-n 400 --discard-n 400 --n 40 --exclude ID [--exclude ID ...]`. When `--year` is given the command calls `draw_gold` and passes the result as `candidates`; without `--year` it behaves as today. `--threshold` defaults to 3.

- [ ] **Step 1: Write the failing tests**

```python
def test_selection_from_candidates_needs_no_judge_rows(tmp_path):
    p = tmp_path / "g.csv"  # does not exist
    cands = [(f"f{i:02}", "flagged") for i in range(10)] + [(f"d{i:02}", "discard") for i in range(10)]
    sel = select_for_audit(p, n=6, seed=1, candidates=cands)
    assert sum(1 for _, pile in sel if pile == "flagged") == 3
    assert sum(1 for _, pile in sel if pile == "discard") == 3
    assert sel == select_for_audit(p, n=6, seed=1, candidates=list(reversed(cands)))


def test_excluded_ids_are_never_selected(tmp_path):
    p = tmp_path / "g.csv"
    cands = [("f00", "flagged"), ("f01", "flagged"), ("d00", "discard"), ("d01", "discard")]
    sel = select_for_audit(p, n=4, seed=1, candidates=cands, exclude={"f00", "d01"})
    assert {d for d, _ in sel} == {"f01", "d00"}


def test_candidates_already_labelled_by_the_owner_are_skipped(tmp_path):
    # append a HUMAN row for f00, then select with candidates containing f00
    assert "f00" not in {d for d, _ in sel}
```

In the CLI test style of `test_cli_label_wires_selection_and_session`: `test_cli_label_with_year_draws_from_the_piles` (monkeypatch `draw_gold`; assert it was called with `flagged_n=400, discard_n=400, years=[2025, 2026]` and that its ids, minus the `--exclude` ones, reach the session) and `test_cli_label_refuses_while_the_review_runs` (`draw_gold` raises `ReviewStillRunning`; exit code 1 and the message on stderr, no session started).

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_label.py -q`
Expected: the new tests fail on the unknown keywords.

- [ ] **Step 3: Implement**

The message for an empty selection names the right cause in each mode: with candidates, "nothing to label: every drawn abstract is labelled or excluded". Replace the `--prompt-version` help text, which says the option is unused. The existing test `test_display_shows_only_text_and_counter` must still pass: nothing about the pile, the score or the judge is printed.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_label.py tests/test_cli.py -q`
Expected: all pass.

- [ ] **Step 5: Commit** (`Phase 7 task 5: label can pick from the draw, before the judge`)

---

### Task 4: The flagged citation seeds are judged too

**Files:**
- Modify: `src/elusion/evaluate/gold.py` (new `with_seeds`)
- Modify: `src/elusion/collect/abstracts.py` (new `doc_ids_by_work`)
- Modify: `src/elusion/flows/evaluate.py` (`judge`), `src/elusion/cli.py` (`judge` command)
- Test: `tests/test_gold.py`, `tests/test_abstracts.py`, `tests/test_judge_flow.py`

**Interfaces:**
- Consumes: `custody.seed_works(engine) -> dict[str, Seed]` (exists; keyed by work id).
- Produces:
  - `doc_ids_by_work(abstracts_dir: Path) -> dict[str, set[str]]`: every `doc_id` stored for each `work_id`, read from the `doc_id` and `work_id` columns of the abstracts Parquet files.
  - `with_seeds(gold: list[tuple[str, str]], scores: Mapping[str, int], threshold: int, seed_doc_ids: Collection[str]) -> list[tuple[str, str]]`: `gold`, followed by `(doc_id, "seed")` for each seed document scoring at or above `threshold` that is not already in `gold`, sorted by id.
  - `judge(..., seeds: bool = False)`; CLI `elusion judge --seeds`. With `seeds`, the flow adds the flagged seeds of the pair after the draw. `--seeds` with `--only-labeled` is rejected by the CLI as a usage error: the seeds are not part of the owner's 40.

- [ ] **Step 1: Write the failing tests**

```python
def test_with_seeds_adds_flagged_seeds_outside_the_draw():
    gold = [("f1", "flagged"), ("d1", "discard")]
    scores = {"f1": 4, "f2": 3, "f3": 4, "d1": 0, "d2": 1}
    got = with_seeds(gold, scores, 3, seed_doc_ids={"f1", "f3", "d2", "zz"})
    assert got == [("f1", "flagged"), ("d1", "discard"), ("f3", "seed")]
```

`"f1"` stays `flagged` (already drawn), `"d2"` is a discarded seed and is not judged, `"zz"` has no score and is ignored. In `tests/test_abstracts.py`: `test_doc_ids_by_work_reads_every_year_file` (two works written with `add_works` across two years; each work id maps to its doc id). In `tests/test_judge_flow.py`: `test_seed_rows_carry_the_seed_pile` (gold passed in with a `("s1", "seed")` entry; the CSV row for `s1` has `pile == "seed"`).

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_gold.py tests/test_abstracts.py tests/test_judge_flow.py -q`
Expected: the new tests fail on the missing names.

- [ ] **Step 3: Implement**

`Pile` in `gold.py` becomes `Literal["flagged", "discard", "seed"]`. In `judge`, when `seeds` is set and the gold was drawn (not passed in), the seed document ids are the union of `doc_ids_by_work(...)[w]` for every `w` in `seed_works(engine)`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_gold.py tests/test_abstracts.py tests/test_judge_flow.py -q`
Expected: all pass.

- [ ] **Step 5: Commit** (`Phase 7 task 5: the judge also reads the flagged citation seeds`)

---

### Task 5: The grader reads the same flagged sample

**Files:**
- Modify: `src/elusion/flows/grade.py` (`grade`), `src/elusion/cli.py` (`grade` command)
- Test: `tests/test_grade_flow.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `split_piles(..., flagged_n=...)` from Task 1; `scores_for` and `verdicts_for` (exist).
- Produces: `grade(..., flagged_n: int | None = None, seed: int = DEFAULT_SEED)`; CLI `elusion grade --flagged-n INT --seed INT`.

- [ ] **Step 1: Write the failing test** in the style of the existing grade flow tests that read verdicts from a fake engine:

```python
def test_flagged_limit_grades_only_the_drawn_ids():
    # the pair has 10 flagged verdicts; grade(..., flagged_n=4, seed=7)
    drawn = {d for d, p in split_piles(scores, 3, 0, 7, flagged_n=4) if p == "flagged"}
    assert graded_doc_ids == drawn
```

And `test_grade_cli_passes_flagged_n_and_seed` in `tests/test_cli.py`.

- [ ] **Step 2: Run and see it fail**

Run: `uv run pytest tests/test_grade_flow.py tests/test_cli.py -q`

- [ ] **Step 3: Implement**

When `flagged_n` is set and `verdicts` was read from Postgres, keep only the verdicts whose id is in the flagged part of `split_piles(scores_for(...), threshold, 0, seed, flagged_n)`. Verdicts passed in by a test are not narrowed.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_grade_flow.py tests/test_cli.py -q`
Expected: all pass.

- [ ] **Step 5: Commit** (`Phase 7 task 5: grade reads the flagged sample`)

---

### Task 6: The report from a flagged sample

**Files:**
- Modify: `src/elusion/flows/evaluate.py` (`evaluate`, `MissingLabels` message)
- Modify: `src/elusion/evaluate/report.py` (`RunReport`, `render`)
- Modify: `src/elusion/evaluate/tracking.py` (`run_params`)
- Modify: `src/elusion/cli.py` (`evaluate` command)
- Modify: `CLAUDE.md` (commands block), `docs/plans/phase-7-literature.md` (the "Goal" block and a result line under task 5)
- Test: `tests/test_evaluate_flow.py`, `tests/test_report.py`, `tests/test_tracking.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `split_piles(..., flagged_n=...)` from Task 1.
- Produces:
  - `evaluate(..., flagged_n: int | None = None)`; CLI `elusion evaluate --flagged-n INT`.
  - `RunReport.flagged_n: int | None = None` and `RunReport.precision_by_score: dict[int, Estimate] | None = None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_evaluate_flow.py`, with the fake engine and CSV helpers already there:

```python
def test_precision_comes_from_the_flagged_draw_only(tmp_path):
    # 20 flagged scores; judge rows for the 5 drawn ids (3 relevant) and for one
    # flagged id outside the draw, labelled relevant with pile "seed"
    report = ...  # evaluate(..., flagged_n=5, log_mlflow=False)
    assert (report.precision.k, report.precision.n) == (3, 5)
    assert report.n_flagged == 20  # recall and richness keep the whole pile's size


def test_missing_label_in_the_flagged_draw_names_the_flag(tmp_path):
    with pytest.raises(MissingLabels, match="--flagged-n"):
        evaluate(..., flagged_n=5, log_mlflow=False)  # one drawn id has no judge row


def test_precision_by_score_splits_the_same_sample(tmp_path):
    # the draw holds two ids scored 3 (one relevant) and three scored 4 (two relevant)
    assert {s: (e.k, e.n) for s, e in report.precision_by_score.items()} == {3: (1, 2), 4: (2, 3)}


def test_without_a_flagged_limit_nothing_changes(tmp_path):
    assert report.flagged_n is None and report.precision_by_score is None
```

In `tests/test_report.py`: `test_flagged_sample_line_and_by_score_lines_render` (with `flagged_n=400` the text contains `flagged sample: 400 drawn with seed` and one line each for `score 3` and `score 4` with k, n and the interval) and `test_render_is_unchanged_without_a_flagged_limit` (the rendered text of the existing fixture report equals what it rendered before this task; pin it by asserting neither `flagged sample` nor `score 3` appears and by keeping every existing assertion green). In `tests/test_tracking.py`: `flagged_n` is among the params only when it is set.

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest tests/test_evaluate_flow.py tests/test_report.py tests/test_tracking.py -q`

- [ ] **Step 3: Implement**

In `evaluate`, one call to `split_piles(scores, threshold, discard_n, seed, flagged_n)` gives both draws. Precision, the grades join and `relevant` use the flagged draw; `n_flagged` and `n_discard` stay the sizes of the whole piles. When ids of the flagged draw lack a judge label the message is: `"<k> flagged paragraphs of the draw have no <model> label in <csv>, e.g. <id>; re-run `elusion judge` with the same --flagged-n and seed"` (without a limit the message stays as it is today). `precision_by_score` is `metrics.proportion` over the drawn rows of each score from `threshold` to 4, set only when `flagged_n` is given. In `render`, the flagged-sample line goes directly above the discard-sample line and the by-score lines go below the metrics table, under the sentence "Precision by score, from the same flagged sample; a description, not a separate estimate of the population."

- [ ] **Step 4: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check && uv run ruff format --check`
Expected: all pass.

- [ ] **Step 5: Update the commands**

In `CLAUDE.md`'s commands block, after the phase 7 review line, in this order (each prefixed in use by `op run --env-file=.env.tpl --` except `label`):

```
uv run elusion label --criterion criteria/auditable-decisions.md --prompt-version v1 --year 2025 --year 2026 --review-model gpt-6-luna --flagged-n 400 --discard-n 400 --n 40 --exclude b5a579121f7c0f6a --exclude 4839dd2f05549e59 --exclude 11e01572681982b3 --exclude 7e84529379fbbf14 --exclude cb52fb2ee7bc1a71 --exclude c70aa5cfcdacaff1
uv run elusion judge --criterion criteria/auditable-decisions.md --prompt-version v1 --year 2025 --year 2026 --review-model gpt-6-luna --flagged-n 400 --discard-n 400 --only-labeled   # the owner's 40 first
uv run elusion judge --criterion criteria/auditable-decisions.md --prompt-version v1 --year 2025 --year 2026 --review-model gpt-6-luna --flagged-n 400 --discard-n 400 --seeds          # then the rest
```

Put the same three lines in the "Goal" block of `docs/plans/phase-7-literature.md` in place of its one `judge` line, and add a dated result line under task 5 saying what was built.

- [ ] **Step 6: Commit** (`Phase 7 task 5: evaluate and the report read the flagged sample`)

---

## After the last task

Not code, and the owner's to start: once the review job has finished, `label` (40), then `judge --only-labeled`, then the agreement is read with the owner (spec, step 5). Check before the first run that `--year 2025 --year 2026` covers every scored abstract: `scores_for` with those years should return as many ids as the pair has scores.
