"""The audit web pages, driven through Flask's test client against the throwaway database.

The test that matters most is the blindness one: before the owner has given a verdict on
every paper, no page may show anything of the reader's reading.
"""

import uuid

import pytest

from elusion.board import audit, log
from elusion.board.batch import Paper
from elusion.board.web import create_app

QUOTE = "zebra-striped stopping threshold"
RATIONALE = "wombat rationale unique to the reading"
WORDING = "Does the paper say when a review should stop?"


def doc(i: int) -> str:
    return f"{i:016x}"


def paper(i: int, text: str | None = None) -> Paper:
    return Paper(
        doc_id=doc(i),
        work_id=f"W{i}",
        title=f"Title of paper {i}",
        publication_date="2025-01-01",
        text=text or f"Paper {i} sets a {QUOTE} for its review.",
    )


@pytest.fixture
def concept(board_engine) -> str:
    slug = f"c{uuid.uuid4().hex[:10]}"
    log.add_concept(board_engine, slug, WORDING, "owner")
    return slug


def read(engine, concept, p: Paper, verdict: str, quote: str | None = QUOTE) -> int:
    return log.add_reading(
        engine,
        concept=concept,
        version=1,
        doc_id=p.doc_id,
        verdict=verdict,
        quote=quote if verdict != "failed" else None,
        rationale=RATIONALE,
        model="reader-model-9",
        prompt_version="first-read/v1",
        text=p.text,
    )


def make(engine, concept, n=3, held_back=(), verdicts=None, texts=None):
    """An app over n papers, each read by the same reader; `verdicts` are the readings."""
    verdicts = verdicts or ["holds"] * n
    papers = {doc(i): paper(i, (texts or {}).get(i)) for i in range(n)}
    for i, v in enumerate(verdicts):
        read(engine, concept, papers[doc(i)], v, quote=QUOTE if v == "holds" else None)
    app = create_app(engine, concept, papers, {doc(i) for i in held_back})
    app.testing = True
    return app, papers


def order_of(concept, n):
    return audit.audit_order([doc(i) for i in range(n)], concept, 1)


def give_all(client, concept, verdicts):
    """Post a verdict for each paper (a dict of doc_id -> verdict)."""
    for d, v in verdicts.items():
        assert client.post(f"/audit/{d}", data={"verdict": v}).status_code == 303


def page(client, url, status=200) -> str:
    response = client.get(url)
    assert response.status_code == status, response.status_code
    return response.get_data(as_text=True)


# -- the blind step ------------------------------------------------------------------------


def test_the_audit_page_shows_the_concept_and_one_paper_but_nothing_of_the_reading(
    board_engine, concept
) -> None:
    app, papers = make(board_engine, concept, n=3)
    html = page(app.test_client(), "/audit")
    first = order_of(concept, 3)[0]
    assert WORDING in html
    assert "paper 1 of 3" in html.lower()
    assert papers[first].title in html
    assert papers[first].text in html  # the abstract, which holds the quote's words
    assert html.count(QUOTE) == 1  # ...once, there, and not marked
    for secret in ("<mark", RATIONALE, "reader-model-9", "first-read/v1"):
        assert secret not in html
    for button in ("Holds", "Does not hold", "Skip"):
        assert f">{button}<" in html
    assert f'action="/audit/{first}"' in html


def test_no_page_before_the_blind_step_ends_shows_a_reading(board_engine, concept) -> None:
    app, papers = make(board_engine, concept, n=3)
    client = app.test_client()
    first = order_of(concept, 3)[0]
    client.post(f"/audit/{first}", data={"verdict": "does_not_hold"})  # disagrees with reader
    for url in ("/audit", "/audit/disagreements", "/audit/summary"):
        response = client.get(url)
        html = response.get_data(as_text=True)
        assert RATIONALE not in html
        assert "reader-model-9" not in html
        assert "<mark" not in html
    post = client.post(f"/audit/disagreements/{first}", data={"verdict": "holds", "reason": "x"})
    assert post.status_code == 409


def test_posting_a_verdict_stores_one_audit_entry_and_moves_on(board_engine, concept) -> None:
    app, papers = make(board_engine, concept, n=3)
    client = app.test_client()
    first, second, _ = order_of(concept, 3)
    response = client.post(f"/audit/{first}", data={"verdict": "holds"})
    assert response.status_code == 303
    assert response.headers["Location"].endswith("/audit")
    [entry] = log.entries(board_engine, concept, kind="audit")
    assert (entry.doc_id, entry.verdict, entry.actor) == (first, "holds", "owner")
    html = page(client, "/audit")
    assert papers[second].title in html
    assert "paper 2 of 3" in html.lower()


def test_skip_stores_a_skip_and_the_paper_comes_round_again(board_engine, concept) -> None:
    app, papers = make(board_engine, concept, n=2)
    client = app.test_client()
    first, second = order_of(concept, 2)
    client.post(f"/audit/{first}", data={"verdict": "skip"})
    assert papers[second].title in page(client, "/audit")
    client.post(f"/audit/{second}", data={"verdict": "skip"})
    assert papers[first].title in page(client, "/audit")


def test_a_second_verdict_is_409_and_a_bad_one_400_and_an_unknown_paper_404(
    board_engine, concept
) -> None:
    app, _ = make(board_engine, concept, n=2)
    client = app.test_client()
    first = order_of(concept, 2)[0]
    assert client.post(f"/audit/{first}", data={"verdict": "holds"}).status_code == 303
    assert client.post(f"/audit/{first}", data={"verdict": "does_not_hold"}).status_code == 409
    assert client.post(f"/audit/{first}", data={"verdict": "maybe"}).status_code == 400
    assert client.post(f"/audit/{first}", data={}).status_code == 400
    assert client.post(f"/audit/{doc(99)}", data={"verdict": "holds"}).status_code == 404
    assert len(log.entries(board_engine, concept, kind="audit")) == 1


def test_a_held_back_paper_is_never_served_and_its_post_is_404(board_engine, concept) -> None:
    app, papers = make(board_engine, concept, n=4, held_back=(1,))
    client = app.test_client()
    shown = []
    for _ in range(3):
        html = page(client, "/audit")
        assert "of 3" in html
        assert papers[doc(1)].title not in html
        d = next(d for d, p in papers.items() if p.title in html)
        shown.append(d)
        client.post(f"/audit/{d}", data={"verdict": "holds"})
    assert sorted(shown) == [doc(0), doc(2), doc(3)]
    assert client.post(f"/audit/{doc(1)}", data={"verdict": "holds"}).status_code == 404
    assert client.get("/audit").status_code == 302


def test_the_root_redirects_to_the_audit(board_engine, concept) -> None:
    app, _ = make(board_engine, concept, n=1)
    response = app.test_client().get("/")
    assert response.status_code == 302 and response.headers["Location"].endswith("/audit")


# -- after the blind step ------------------------------------------------------------------


def test_disagreements_wait_with_a_409_until_every_paper_has_a_verdict(
    board_engine, concept
) -> None:
    app, _ = make(board_engine, concept, n=3)
    client = app.test_client()
    first, second, _ = order_of(concept, 3)
    html = page(client, "/audit/disagreements", status=409)
    assert "3" in html and 'href="/audit"' in html
    client.post(f"/audit/{first}", data={"verdict": "holds"})
    client.post(f"/audit/{second}", data={"verdict": "skip"})  # a skip is not a verdict
    html = page(client, "/audit/disagreements", status=409)
    assert "2 papers" in html


def test_the_audit_redirects_to_the_disagreements_when_every_paper_has_a_verdict(
    board_engine, concept
) -> None:
    app, papers = make(board_engine, concept, n=2)
    client = app.test_client()
    give_all(client, concept, {d: "holds" for d in papers})
    response = client.get("/audit")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/audit/disagreements")


def finish_with_one_disagreement(client, papers, concept, n):
    """The owner says holds everywhere except the first paper in the order, where he says no."""
    first = order_of(concept, n)[0]
    give_all(client, concept, {d: ("does_not_hold" if d == first else "holds") for d in papers})
    return first


def test_the_disagreement_page_marks_the_quote_in_place_and_escapes_the_abstract(
    board_engine, concept
) -> None:
    hostile = f"<b>bold</b> & so on: it sets a {QUOTE} for <script>alert(1)</script> its review."
    app, papers = make(board_engine, concept, n=2, texts={0: hostile, 1: hostile})
    client = app.test_client()
    first = finish_with_one_disagreement(client, papers, concept, 2)
    html = page(client, "/audit/disagreements")
    assert f"<mark>{QUOTE}</mark>" in html
    assert "&lt;b&gt;bold&lt;/b&gt; &amp; so on: it sets a <mark>" in html
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert papers[first].title in html
    assert RATIONALE in html and "reader-model-9" in html and "first-read/v1" in html
    assert f'action="/audit/disagreements/{first}"' in html
    assert 'name="reason"' in html and 'name="verdict"' in html


def test_an_empty_reason_is_400_and_stores_nothing(board_engine, concept) -> None:
    app, papers = make(board_engine, concept, n=2)
    client = app.test_client()
    first = finish_with_one_disagreement(client, papers, concept, 2)
    for data in ({"verdict": "holds", "reason": "   "}, {"verdict": "holds"}):
        assert client.post(f"/audit/disagreements/{first}", data=data).status_code == 400
    assert (
        client.post(
            f"/audit/disagreements/{first}", data={"verdict": "skip", "reason": "why"}
        ).status_code
        == 400
    )
    assert log.entries(board_engine, concept, kind="decision") == []


def test_a_decision_is_stored_with_its_evidence_and_removes_the_paper_from_the_list(
    board_engine, concept
) -> None:
    app, papers = make(board_engine, concept, n=2)
    client = app.test_client()
    first = finish_with_one_disagreement(client, papers, concept, 2)
    response = client.post(
        f"/audit/disagreements/{first}", data={"verdict": "holds", "reason": "it does set one"}
    )
    assert response.status_code == 303
    [decision] = log.entries(board_engine, concept, kind="decision")
    assert (decision.doc_id, decision.verdict, decision.body, decision.actor) == (
        first,
        "holds",
        "it does set one",
        "owner",
    )
    [blind_entry] = [
        e for e in log.entries(board_engine, concept, kind="audit") if e.doc_id == first
    ]
    [reading_entry] = [
        e for e in log.entries(board_engine, concept, kind="reading") if e.doc_id == first
    ]
    assert sorted(decision.detail["rests_on"]) == sorted(
        [blind_entry.entry_id, reading_entry.entry_id]
    )
    done = client.get("/audit/disagreements")
    assert done.status_code == 302 and done.headers["Location"].endswith("/audit/summary")


def test_a_failed_reading_is_not_a_disagreement(board_engine, concept) -> None:
    app, papers = make(board_engine, concept, n=2, verdicts=["failed", "failed"])
    client = app.test_client()
    give_all(client, concept, {d: "holds" for d in papers})
    response = client.get("/audit/disagreements")
    assert response.status_code == 302 and response.headers["Location"].endswith("/audit/summary")


def test_a_decision_on_a_paper_that_is_not_a_disagreement_is_404(board_engine, concept) -> None:
    app, papers = make(board_engine, concept, n=2)
    client = app.test_client()
    first = finish_with_one_disagreement(client, papers, concept, 2)
    other = next(d for d in papers if d != first)
    response = client.post(
        f"/audit/disagreements/{other}", data={"verdict": "holds", "reason": "because"}
    )
    assert response.status_code == 404
    assert log.entries(board_engine, concept, kind="decision") == []


# -- the summary ---------------------------------------------------------------------------


def test_the_summary_shows_only_progress_before_the_blind_step_ends(board_engine, concept) -> None:
    app, _ = make(board_engine, concept, n=3)
    client = app.test_client()
    client.post(f"/audit/{order_of(concept, 3)[0]}", data={"verdict": "holds"})
    html = page(client, "/audit/summary")
    assert "1 of 3" in html
    assert "95%" not in html and "Agreement" not in html


def test_the_summary_gives_agreement_and_precision_in_words(board_engine, concept) -> None:
    app, papers = make(
        board_engine, concept, n=4, verdicts=["holds", "holds", "does_not_hold", "failed"]
    )
    client = app.test_client()
    # blind: holds, no, no, holds -> agree on d0 and d2; d1 disagrees; d3's reading failed
    give_all(
        client,
        concept,
        {doc(0): "holds", doc(1): "does_not_hold", doc(2): "does_not_hold", doc(3): "holds"},
    )
    html = page(client, "/audit/summary")
    assert "2 of 3" in html  # agreement: 2 of the 3 comparable papers
    assert "66.7%" in html and "95% interval" in html
    assert "2 of 4" in html  # blind holds d0 and d3 of 4 audited
    assert "1 paper" in html and "failed" in html  # the failed reading
    assert "1 disagreement" in html and "not yet settled" in html
    client.post(f"/audit/disagreements/{doc(1)}", data={"verdict": "holds", "reason": "stops"})
    html = page(client, "/audit/summary")
    assert "3 of 4" in html  # the decision moved the word search's precision
    assert "2 of 3" in html  # ...and left the blind agreement alone


def test_the_summary_counts_the_papers_held_back(board_engine, concept) -> None:
    app, papers = make(board_engine, concept, n=3, held_back=(2,))
    client = app.test_client()
    give_all(client, concept, {doc(0): "holds", doc(1): "holds"})
    html = page(client, "/audit/summary")
    assert "held back" in html and "1 paper" in html


def test_a_held_back_id_with_no_reading_is_not_counted(board_engine, concept) -> None:
    _, papers = make(board_engine, concept, n=2)
    app = create_app(board_engine, concept, papers, {doc(1), "f" * 16})
    client = app.test_client()
    give_all(client, concept, {doc(0): "holds"})
    html = page(client, "/audit/summary")
    assert "0 papers" not in html.split("held back")[1][:80]
    assert "1 paper" in html.split("held back")[1][:80]


# -- the page is local: another site must not post to it -----------------------------------


def test_a_post_from_another_origin_is_403_and_stores_nothing(board_engine, concept) -> None:
    app, papers = make(board_engine, concept, n=2)
    client = app.test_client()
    first = order_of(concept, 2)[0]
    foreign = client.post(
        f"/audit/{first}", data={"verdict": "holds"}, headers={"Origin": "http://evil.example"}
    )
    assert foreign.status_code == 403
    assert (
        client.post(
            f"/audit/{first}", data={"verdict": "holds"}, headers={"Origin": "null"}
        ).status_code
        == 403
    )
    assert log.entries(board_engine, concept, kind="audit") == []
    own = client.post(
        f"/audit/{first}", data={"verdict": "holds"}, headers={"Origin": "http://localhost"}
    )
    assert own.status_code == 303
    assert len(log.entries(board_engine, concept, kind="audit")) == 1


def test_a_foreign_origin_cannot_post_a_decision_either(board_engine, concept) -> None:
    app, papers = make(board_engine, concept, n=2)
    client = app.test_client()
    first = finish_with_one_disagreement(client, papers, concept, 2)
    response = client.post(
        f"/audit/disagreements/{first}",
        data={"verdict": "holds", "reason": "x"},
        headers={"Origin": "http://localhost:9999"},
    )
    assert response.status_code == 403
    assert log.entries(board_engine, concept, kind="decision") == []
