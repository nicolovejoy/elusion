from elusion.config import Settings


def test_run_env_defaults_to_local(monkeypatch) -> None:
    monkeypatch.delenv("ELUSION_RUN_ENV", raising=False)
    assert Settings().run_env == "local"


def test_run_env_comes_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("ELUSION_RUN_ENV", "aks-elusion")
    assert Settings().run_env == "aks-elusion"
