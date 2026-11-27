"""The `ask-eval` tables: idempotent inserts on a throwaway database."""

from elusion.ask import results

RESULT = dict(
    question="q?",
    arm="lexical",
    answer_model="m",
    prompt_version="ask/v1",
    question_id="q01",
    kind="answerable",
    ask_id=1,
    k=5,
    source_hit=True,
)
JUDGEMENT = dict(
    ask_id=1,
    claim_index=0,
    judge_model="j",
    judge_prompt_version="ask/judge-v1",
    supported=True,
    rationale="r",
    served_model="j",
    input_tokens=1,
    output_tokens=1,
    cost_usd=0.5,
)


def test_a_result_is_stored_once_per_key_even_after_the_driver_prepares_the_statement(
    index_engine,
) -> None:
    """psycopg prepares a statement on its fifth use: repeat each insert well past that on one
    pooled connection."""
    first = [results.add_result(index_engine, **RESULT) for _ in range(10)]
    assert first == [True] + [False] * 9
    assert results.add_result(index_engine, **{**RESULT, "arm": "vector"}) is True
    assert results.add_result(index_engine, **{**RESULT, "answer_model": "other"}) is True
    assert results.add_result(index_engine, **{**RESULT, "prompt_version": "ask/v2"}) is True
    assert results.add_result(index_engine, **{**RESULT, "question": "q2?"}) is True
    assert results.result_keys(index_engine, "m", "ask/v1") == {
        ("q?", "lexical"),
        ("q?", "vector"),
        ("q2?", "lexical"),
    }
    assert results.result_keys(index_engine, "m", "ask/v1") != results.result_keys(
        index_engine, "other", "ask/v1"
    )
    stored = results.results_for(index_engine, "m", "ask/v1")
    assert [(r.question, r.arm, r.source_hit, r.ask_id) for r in stored] == [
        ("q?", "lexical", True, 1),
        ("q2?", "lexical", True, 1),
        ("q?", "vector", True, 1),
    ]


def test_a_judgement_is_stored_once_per_key_even_after_the_driver_prepares_the_statement(
    index_engine,
) -> None:
    first = [results.add_judgement(index_engine, **JUDGEMENT) for _ in range(10)]
    assert first == [True] + [False] * 9
    assert results.add_judgement(index_engine, **{**JUDGEMENT, "claim_index": 1, "supported": None})
    assert results.add_judgement(index_engine, **{**JUDGEMENT, "judge_model": "j2"})
    assert results.judged(index_engine, "j", "ask/judge-v1") == {(1, 0), (1, 1)}
    found = results.judgements(index_engine, "j", "ask/judge-v1")
    assert found[(1, 0)] == results.Judgement(True, "r", 0.5)
    assert found[(1, 1)].supported is None
    assert results.judged(index_engine, "j", "ask/judge-v2") == set()


def test_a_result_of_an_older_retrieval_version_is_kept_but_not_found(index_engine) -> None:
    """A fixed search arm must be asked again: its old rows stay as history, outside the key."""
    old = {**RESULT, "retrieval_version": "r0", "ask_id": 7}
    assert results.add_result(index_engine, **old) is True
    assert results.result_keys(index_engine, "m", "ask/v1") == set()
    assert results.results_for(index_engine, "m", "ask/v1") == []
    assert results.add_result(index_engine, **RESULT) is True  # the current version, same question
    assert results.result_keys(index_engine, "m", "ask/v1") == {("q?", "lexical")}
    assert [r.ask_id for r in results.results_for(index_engine, "m", "ask/v1")] == [1]


def test_ensure_schema_gives_rows_stored_before_the_version_column_r1(index_engine) -> None:
    """The first run's table had no retrieval_version: its rows become r1 and keep their data."""
    import sqlalchemy as sa

    with index_engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE ask_eval_results"))
        conn.execute(
            sa.text(
                "CREATE TABLE ask_eval_results (question text, arm text, answer_model text, "
                "prompt_version text, question_id text NOT NULL, kind text NOT NULL, "
                "ask_id bigint NOT NULL, k smallint NOT NULL, source_hit boolean, "
                "evaluated_at timestamptz NOT NULL DEFAULT now(), "
                "PRIMARY KEY (question, arm, answer_model, prompt_version))"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO ask_eval_results VALUES ('q?', 'vector', 'm', 'ask/v1', 'q01', "
                "'answerable', 3, 5, true)"
            )
        )
    results.ensure_schema(index_engine)
    results.ensure_schema(index_engine)  # safe to repeat
    assert [r.ask_id for r in results.results_for(index_engine, "m", "ask/v1")] == [3]  # vector r1
    # the key now holds the version: the same question and arm under another version is new
    new = {**RESULT, "arm": "vector", "retrieval_version": "r9"}
    assert results.add_result(index_engine, **new) is True
