from pathlib import Path

from elusion.collect.companies import load_company_names


def test_no_user_agent_means_no_lookup(tmp_path: Path) -> None:
    assert load_company_names(None, tmp_path / "x.json") == {}


def test_reads_cache_without_network(tmp_path: Path) -> None:
    cache = tmp_path / "tickers.json"
    cache.write_text('{"0": {"cik_str": 88121, "ticker": "SEB", "title": "SEABOARD CORP"}}')
    assert load_company_names("test agent", cache) == {"88121": "SEABOARD CORP"}
