"""The harvest's search terms: the owner's decision of 2026-11-16, as data."""

from elusion.collect.terms import AGENT_PHRASES, FROM_DATE, OPENALEX_CORPUS, TERMS, Term


def test_sixteen_terms_nine_stemmed_and_seven_exact() -> None:
    assert len(TERMS) == 16
    assert len({t.query for t in TERMS}) == 16
    assert [t.form for t in TERMS].count("stemmed") == 9
    assert [t.form for t in TERMS].count("exact") == 7


def test_the_nine_original_terms_are_unchanged_and_stemmed() -> None:
    stemmed = [t.query for t in TERMS if t.form == "stemmed"]
    assert stemmed == [
        '"technology-assisted review"',
        '"high-recall retrieval"',
        '"citation screening"',
        '"e-discovery" OR "electronic discovery"',
        '"LLM-as-a-judge"',
        '"algorithmic accountability"',
        '"explainable AI" AND audit',
        '"automated decision-making"',
        '"audit trail"',
    ]


def test_five_exact_terms_pair_every_agent_phrase_with_a_word_group() -> None:
    agent_terms = [t for t in TERMS if t.query.startswith("(" + '"AI agent"')]
    assert len(agent_terms) == 5
    for term in agent_terms:
        assert term.form == "exact"
        for phrase in AGENT_PHRASES:
            assert f'"{phrase}"' in term.query
        assert ") AND (" in term.query


def test_agent_phrases_spell_out_their_plurals() -> None:
    # Exact search does no stemming, so "AI agents" has to be asked for by name.
    for phrase in ("AI agent", "AI agents", "agentic AI", "agentic workflows"):
        assert phrase in AGENT_PHRASES
    assert len(AGENT_PHRASES) == 13


def test_the_window_and_the_openalex_corpus() -> None:
    assert FROM_DATE == "2025-01-01"
    assert OPENALEX_CORPUS == "core"
    assert Term("q", "exact").form == "exact"
