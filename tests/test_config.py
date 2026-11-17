from elusion.config import Settings


def test_run_env_defaults_to_local(monkeypatch) -> None:
    monkeypatch.delenv("ELUSION_RUN_ENV", raising=False)
    assert Settings().run_env == "local"


def test_run_env_comes_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("ELUSION_RUN_ENV", "aks-elusion")
    assert Settings().run_env == "aks-elusion"


def test_openalex_key_is_read_from_the_environment_and_unset_by_default(monkeypatch) -> None:
    from elusion.config import Settings

    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    assert Settings().openalex_api_key is None
    monkeypatch.setenv("OPENALEX_API_KEY", "k-123")
    assert Settings().openalex_api_key == "k-123"
