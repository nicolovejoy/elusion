"""Each criterion belongs to one corpus, and the two corpora share no topic or directory."""

from pathlib import Path

import pytest

from elusion.config import settings
from elusion.corpora import EDGAR, OPENALEX, UnknownCriterion, corpus_for


def test_each_criterion_names_its_corpus() -> None:
    assert corpus_for("cloud-concentration") is EDGAR
    assert corpus_for("auditable-decisions") is OPENALEX


def test_a_criterion_with_no_corpus_is_refused_and_told_where_to_add_it() -> None:
    with pytest.raises(UnknownCriterion, match="'new-criterion'.*corpora.py"):
        corpus_for("new-criterion")


def test_the_10k_corpus_keeps_its_topic_and_directory() -> None:
    assert EDGAR.name == "edgar" and EDGAR.topic == "documents.ingested"
    assert EDGAR.text_dir(settings.data_dir) == settings.corpus_dir


def test_the_two_corpora_share_nothing() -> None:
    assert OPENALEX.name == "openalex" and OPENALEX.topic == "abstracts.ingested"
    assert OPENALEX.text_dir(Path("data")) == Path("data/abstracts")
    assert EDGAR.topic != OPENALEX.topic
    assert EDGAR.text_dir(Path("data")) != OPENALEX.text_dir(Path("data"))
