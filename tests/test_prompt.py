import pytest

from elusion.review.prompt import Prompt, load_prompt


def test_loads_v1_with_exact_version_string() -> None:
    prompt = load_prompt("cloud-concentration", "v1")
    assert isinstance(prompt, Prompt)
    assert prompt.version == "cloud-concentration/v1"
    assert "verbatim" in prompt.text
    for field in ("score", "rationale", "quote"):
        assert field in prompt.text


def test_default_version_is_v1() -> None:
    assert load_prompt("cloud-concentration") == load_prompt("cloud-concentration", "v1")


def test_missing_version_raises_naming_path() -> None:
    with pytest.raises(FileNotFoundError, match="v99"):
        load_prompt("cloud-concentration", "v99")


def test_missing_criterion_raises() -> None:
    with pytest.raises(FileNotFoundError, match="no-such-criterion"):
        load_prompt("no-such-criterion")
