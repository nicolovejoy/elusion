import pytest

from elusion.review.prompt import Prompt, load_prompt


def test_loads_v1_with_exact_version_string() -> None:
    prompt = load_prompt("cloud-concentration", "v1")
    assert isinstance(prompt, Prompt)
    assert prompt.version == "cloud-concentration/v1"
    assert "verbatim" in prompt.text
    for field in ("score", "rationale", "quote"):
        assert field in prompt.text


def test_loads_v2_with_exact_version_string() -> None:
    prompt = load_prompt("cloud-concentration", "v2")
    assert prompt.version == "cloud-concentration/v2"
    assert "verbatim" in prompt.text
    for field in ("score", "rationale", "quote"):
        assert field in prompt.text
    assert load_prompt("cloud-concentration", "v1").text != prompt.text


def test_loads_judge_v1_with_exact_version_string() -> None:
    prompt = load_prompt("cloud-concentration", "judge-v1")
    assert prompt.version == "cloud-concentration/judge-v1"
    for field in ("relevant", "rationale"):
        assert field in prompt.text
    assert "0-4" not in prompt.text  # yes/no judge, no scale


def test_loads_grade_v1_with_exact_version_string() -> None:
    prompt = load_prompt("cloud-concentration", "grade-v1")
    assert prompt.version == "cloud-concentration/grade-v1"
    for field in ("sound", "rationale"):
        assert field in prompt.text
    assert "Do not re-score" in prompt.text
    assert "label" not in prompt.text.lower()  # the grader never sees or hears of a label


def test_default_version_is_v1() -> None:
    assert load_prompt("cloud-concentration") == load_prompt("cloud-concentration", "v1")


def test_missing_version_raises_naming_path() -> None:
    with pytest.raises(FileNotFoundError, match="v99"):
        load_prompt("cloud-concentration", "v99")


def test_missing_criterion_raises() -> None:
    with pytest.raises(FileNotFoundError, match="no-such-criterion"):
        load_prompt("no-such-criterion")


def test_loads_v3_with_exact_version_string_and_gap_rules() -> None:
    prompt = load_prompt("cloud-concentration", "v3")
    assert prompt.version == "cloud-concentration/v3"
    for field in ("score", "rationale", "quote"):
        assert field in prompt.text
    for phrase in ("SaaS", "ISP", "outsourc", "on-site"):
        assert phrase in prompt.text
    assert "Generic IT outsourcing" in prompt.text  # unique to v3
    assert load_prompt("cloud-concentration", "v2").text != prompt.text


def test_the_literature_criterion_has_its_three_prompts_and_its_file() -> None:
    from pathlib import Path

    from elusion.corpora import OPENALEX, corpus_for

    for version, field in (
        ("v1", "- score:"),
        ("judge-v1", "- relevant:"),
        ("grade-v1", "- sound:"),
    ):
        prompt = load_prompt("auditable-decisions", version)
        assert prompt.version == f"auditable-decisions/{version}"
        assert field in prompt.text and "- rationale:" in prompt.text
        assert "title and abstract" in prompt.text
    assert "- quote:" in load_prompt("auditable-decisions", "v1").text
    assert corpus_for("auditable-decisions") is OPENALEX
    assert (Path(__file__).parent.parent / "criteria" / "auditable-decisions.md").is_file()


def test_latest_judge_version_is_the_highest_on_disk_and_v1_without_files() -> None:
    from elusion.review.prompt import latest_judge_version

    assert latest_judge_version("cloud-concentration") == "judge-v1"
    assert latest_judge_version("auditable-decisions") == "judge-v3"
    assert latest_judge_version("no-such-criterion") == "judge-v1"


def test_judge_v2_of_the_literature_criterion_keeps_theory_and_the_answer_format() -> None:
    prompt = load_prompt("auditable-decisions", "judge-v2")
    assert prompt.version == "auditable-decisions/judge-v2"
    for phrase in ("mental model", "Theory,", "in passing", "If you are unsure, keep"):
        assert phrase in prompt.text
    assert "- relevant:" in prompt.text and "- rationale:" in prompt.text


def test_judge_v3_is_judge_v2_without_the_keep_if_unsure_clause() -> None:
    v2 = load_prompt("auditable-decisions", "judge-v2").text
    v3 = load_prompt("auditable-decisions", "judge-v3").text
    assert v3 == v2.replace("If you are unsure, keep the paper: answer relevant.\n\n", "")
    assert "unsure" not in v3
