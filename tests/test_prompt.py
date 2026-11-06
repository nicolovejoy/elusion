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
    assert load_prompt("cloud-concentration", "v2").text != prompt.text
