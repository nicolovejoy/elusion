from elusion.collect.split import doc_id, split_paragraphs

LONG = " ".join(f"word{i}" for i in range(40))
SHORT = "Item 1A. Risk Factors"


def test_splits_on_newlines_and_drops_fragments() -> None:
    text = f"{SHORT}\n{LONG}\n\n  {LONG} \n●bullet fragment\n{LONG}"
    assert split_paragraphs(text) == [LONG, LONG, LONG]


def test_min_words_is_inclusive() -> None:
    thirty = " ".join(["w"] * 30)
    twenty_nine = " ".join(["w"] * 29)
    assert split_paragraphs(f"{thirty}\n{twenty_nine}", min_words=30) == [thirty]


def test_empty_and_whitespace_only() -> None:
    assert split_paragraphs("") == []
    assert split_paragraphs("\n \n") == []


def test_doc_id_is_deterministic_and_text_sensitive() -> None:
    a = doc_id("88121", 2019, LONG)
    assert a == doc_id("88121", 2019, LONG)
    assert len(a) == 16
    assert a != doc_id("88121", 2020, LONG)
    assert a != doc_id("88121", 2019, LONG + " x")
