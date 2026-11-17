"""The OpenAlex client and the functions that turn one work into one document.

No test here calls the network: `httpx.MockTransport` answers the client's requests in
this process. The works are made up, in the shape the API returned on 2026-11-16 for
the fields the harvest selects. Real abstracts are never committed to git.
"""

import hashlib

import httpx
import pytest

from elusion.collect.openalex import (
    SELECT,
    OpenAlex,
    OpenAlexError,
    abstract_doc_id,
    citing_filter,
    document_text,
    rebuild_abstract,
    search_filter,
    to_work,
)
from elusion.collect.terms import Term

# One work as the API serves it: the abstract is an *inverted index*, each word mapped
# to the positions where it occurs. Positions 2 and 9 both hold "the".
RECORDED = {
    "id": "https://openalex.org/W9000000001",
    "doi": "https://doi.org/10.0000/made-up.1",
    "title": "Sampling  the discard pile\nof an automated review",
    "publication_year": 2025,
    "publication_date": "2025-03-14",
    "language": "en",
    "type": "article",
    "updated_date": "2026-11-01T04:12:09.123456",
    "abstract_inverted_index": {
        "We": [0],
        "sample": [1],
        "the": [2, 9],
        "documents": [3],
        "a": [4],
        "classifier": [5],
        "discarded": [6],
        "and": [7],
        "estimate": [8],
        "elusion.": [10],
    },
}
ABSTRACT = "We sample the documents a classifier discarded and estimate the elusion."


def inverted(text: str) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for position, word in enumerate(text.split()):
        index.setdefault(word, []).append(position)
    return index


def work(n: int, abstract: str = "An abstract about audit trails.") -> dict:
    return {
        "id": f"https://openalex.org/W{n}",
        "doi": None,
        "title": f"Work {n}",
        "publication_year": 2026,
        "publication_date": "2026-02-01",
        "language": "en",
        "type": "article",
        "updated_date": "2026-11-01T00:00:00",
        "abstract_inverted_index": inverted(abstract),
    }


def page(results: list[dict], next_cursor: str | None, count: int = 0) -> httpx.Response:
    return httpx.Response(
        200,
        json={"meta": {"count": count, "next_cursor": next_cursor}, "results": results},
        headers={"x-ratelimit-remaining-usd": "0.91"},
    )


def test_rebuilds_an_abstract_from_a_recorded_response() -> None:
    assert rebuild_abstract(RECORDED["abstract_inverted_index"]) == ABSTRACT


def test_rebuild_skips_a_missing_position_and_tolerates_no_index() -> None:
    assert rebuild_abstract({"first": [0], "third": [2]}) == "first third"
    assert rebuild_abstract(None) == ""
    assert rebuild_abstract({}) == ""


def test_document_text_is_title_then_abstract_with_whitespace_collapsed() -> None:
    text = document_text(RECORDED["title"], ABSTRACT)
    assert text == "Sampling the discard pile of an automated review\n\n" + ABSTRACT
    assert document_text(None, ABSTRACT) == ABSTRACT
    assert document_text("   ", ABSTRACT) == ABSTRACT


def test_to_work_carries_the_fields_and_a_hash_of_the_text() -> None:
    w = to_work(RECORDED)
    assert w is not None
    assert w["work_id"] == "W9000000001"
    assert w["title"] == "Sampling the discard pile of an automated review"
    assert (w["year"], w["publication_date"], w["language"]) == (2025, "2025-03-14", "en")
    assert (w["type"], w["doi"]) == ("article", "https://doi.org/10.0000/made-up.1")
    assert w["text"] == document_text(RECORDED["title"], ABSTRACT)
    assert w["content_hash"] == hashlib.sha256(w["text"].encode()).hexdigest()
    assert w["doc_id"] == abstract_doc_id("W9000000001", w["content_hash"])


def test_to_work_stores_a_null_title_as_empty() -> None:
    w = to_work({**RECORDED, "title": None})
    assert w is not None and w["title"] == "" and w["text"] == ABSTRACT


def test_to_work_refuses_a_work_with_no_abstract_or_no_year() -> None:
    assert to_work({**RECORDED, "abstract_inverted_index": None}) is None
    assert to_work({**RECORDED, "abstract_inverted_index": {}}) is None
    assert to_work({**RECORDED, "publication_year": None}) is None


def test_doc_id_is_16_hex_and_follows_both_the_work_and_its_text() -> None:
    a = abstract_doc_id("W1", "a" * 64)
    assert len(a) == 16 and int(a, 16) >= 0
    assert a == abstract_doc_id("W1", "a" * 64)
    assert a != abstract_doc_id("W1", "b" * 64)  # OpenAlex served a different abstract
    assert a != abstract_doc_id("W2", "a" * 64)  # two works with the same text


def test_a_changed_abstract_is_a_new_document_for_the_same_work() -> None:
    before = to_work(work(7, "The first abstract."))
    after = to_work(work(7, "The corrected abstract."))
    assert before["work_id"] == after["work_id"]
    assert before["doc_id"] != after["doc_id"]


def test_search_filter_is_the_window_and_an_abstract() -> None:
    assert search_filter("2025-01-01") == "from_publication_date:2025-01-01,has_abstract:true"


def test_search_follows_the_cursor_to_the_end() -> None:
    requests: list[httpx.Request] = []
    pages = {
        "*": page([work(1), work(2)], "c2", count=3),
        "c2": page([work(3)], "c3", count=3),
        "c3": page([], None, count=3),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return pages[request.url.params["cursor"]]

    client = OpenAlex("k-123", transport=httpx.MockTransport(handler))
    got = list(client.search(Term('"audit trail"', "stemmed"), "2025-01-01"))

    assert [w["id"].rsplit("/", 1)[1] for w in got] == ["W1", "W2", "W3"]
    assert [r.url.params["cursor"] for r in requests] == ["*", "c2", "c3"]
    assert client.pages == 3 and client.remaining_usd == "0.91"


def test_search_sends_the_query_the_decisions_fixed() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return page([], None)

    client = OpenAlex("k-123", transport=httpx.MockTransport(handler))
    list(client.search(Term('"audit trail"', "stemmed"), "2025-01-01"))
    list(client.search(Term('"decision provenance"', "exact"), "2025-01-01"))

    stemmed, exact = (r.url.params for r in seen)
    assert stemmed["search.title_and_abstract"] == '"audit trail"'
    assert "search.title_and_abstract.exact" not in stemmed
    assert exact["search.title_and_abstract.exact"] == '"decision provenance"'
    assert "search.title_and_abstract" not in exact
    for params in (stemmed, exact):
        assert params["filter"] == "from_publication_date:2025-01-01,has_abstract:true"
        assert params["corpus"] == "core"
        assert params["per_page"] == "100"
        assert params["select"] == SELECT


def test_the_key_travels_in_a_header_and_never_in_the_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return page([], None)

    client = OpenAlex("k-123", transport=httpx.MockTransport(handler))
    list(client.search(Term("x", "stemmed"), "2025-01-01"))

    assert seen[0].headers["Authorization"] == "Bearer k-123"
    assert "k-123" not in str(seen[0].url)
    assert "api_key" not in seen[0].url.params


def test_a_client_without_a_key_is_refused() -> None:
    with pytest.raises(OpenAlexError, match="OPENALEX_API_KEY"):
        OpenAlex("")


def test_a_429_waits_for_retry_after_and_then_carries_on() -> None:
    slept: list[float] = []
    answers = iter(
        [
            httpx.Response(429, headers={"Retry-After": "17"}, json={"error": "slow down"}),
            page([work(1)], None),
        ]
    )
    client = OpenAlex(
        "k-123", transport=httpx.MockTransport(lambda r: next(answers)), sleep=slept.append
    )

    got = list(client.search(Term("x", "stemmed"), "2025-01-01"))

    assert len(got) == 1
    assert slept == [18]  # the header's seconds and one more


def test_a_server_error_is_retried_and_a_lasting_one_is_raised() -> None:
    slept: list[float] = []
    answers = iter([httpx.Response(503, text="busy"), page([work(1)], None)])
    client = OpenAlex(
        "k-123", transport=httpx.MockTransport(lambda r: next(answers)), sleep=slept.append
    )
    assert len(list(client.search(Term("x", "stemmed"), "2025-01-01"))) == 1
    assert len(slept) == 1

    always = OpenAlex(
        "k-123",
        transport=httpx.MockTransport(lambda r: httpx.Response(503, text="busy")),
        sleep=slept.append,
        max_attempts=3,
    )
    with pytest.raises(OpenAlexError, match="503"):
        list(always.search(Term("x", "stemmed"), "2025-01-01"))


def test_a_refused_request_is_raised_at_once_without_the_key_in_the_message() -> None:
    slept: list[float] = []
    client = OpenAlex(
        "k-123",
        transport=httpx.MockTransport(lambda r: httpx.Response(400, text="bad filter")),
        sleep=slept.append,
    )
    with pytest.raises(OpenAlexError) as raised:
        list(client.search(Term("x", "stemmed"), "2025-01-01"))
    assert "400" in str(raised.value) and "bad filter" in str(raised.value)
    assert "k-123" not in str(raised.value)
    assert slept == []


def test_max_pages_stops_the_paging_early() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return page([work(len(calls))], f"c{len(calls)}")

    client = OpenAlex("k-123", transport=httpx.MockTransport(handler))
    got = list(client.search(Term("x", "stemmed"), "2025-01-01", max_pages=2))
    assert len(got) == 2 and len(calls) == 2


def test_citing_sample_asks_for_a_seeded_sample_of_the_works_that_cite_the_anchor() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return page([work(1), work(2)], None)

    client = OpenAlex("k-123", transport=httpx.MockTransport(handler))
    got = client.citing_sample("W42", "2025-01-01", n=75, seed=7)

    assert [w["id"].rsplit("/", 1)[1] for w in got] == ["W1", "W2"]
    (params,) = (r.url.params for r in seen)
    assert params["filter"] == "cites:W42,from_publication_date:2025-01-01,has_abstract:true"
    assert params["filter"] == citing_filter("W42", "2025-01-01")
    assert (params["sample"], params["seed"], params["per_page"]) == ("75", "7", "100")
    assert (params["corpus"], params["select"]) == ("core", SELECT)
    assert "cursor" not in params  # a sample is one page; a cursor cannot be combined with it
    assert not any(name.startswith("search") for name in params)


def test_citing_sample_refuses_more_than_one_page() -> None:
    client = OpenAlex("k-123", transport=httpx.MockTransport(lambda r: page([], None)))
    with pytest.raises(ValueError, match="100"):
        client.citing_sample("W42", "2025-01-01", n=101, seed=7)
