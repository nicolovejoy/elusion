"""The audit web pages, driven through Flask's test client against the throwaway database.

The test that matters most is the blindness one: before the owner has given a verdict on
every paper, no page may show anything of the reader's reading.
"""

import re
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


def paper(i: int, text: str | None = None, date: str = "2025-01-01") -> Paper:
    return Paper(
        doc_id=doc(i),
        work_id=f"W{i}",
        title=f"Title of paper {i}",
        publication_date=date,
        text=text or f"Paper {i} sets a {QUOTE} for its review.",
    )


@pytest.fixture
def concept(board_engine) -> str:
    slug = f"c{uuid.uuid4().hex[:10]}"
    log.add_concept(board_engine, slug, WORDING, "owner")
    return slug


def read(engine, concept, p: Paper, verdict: str, quote: str | None = QUOTE, detail=None) -> int:
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
        detail=detail,
    )


def make(engine, concept, n=3, held_back=(), verdicts=None, texts=None, dates=None, details=None):
    """An app over n papers, each read by the same reader; `verdicts` are the readings,
    `details` the readings' detail (a dict by paper number) and `dates` their dates."""
    verdicts = verdicts or ["holds"] * n
    papers = {
        doc(i): paper(i, (texts or {}).get(i), (dates or {}).get(i, "2025-01-01")) for i in range(n)
    }
    for i, v in enumerate(verdicts):
        quote = QUOTE if v == "holds" else None
        read(engine, concept, papers[doc(i)], v, quote=quote, detail=(details or {}).get(i))
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


def test_the_same_verdict_posted_again_redirects_and_stores_nothing_more(
    board_engine, concept
) -> None:
    app, _ = make(board_engine, concept, n=2)
    client = app.test_client()
    first = order_of(concept, 2)[0]
    assert client.post(f"/audit/{first}", data={"verdict": "holds"}).status_code == 303
    again = client.post(f"/audit/{first}", data={"verdict": "holds"})
    assert again.status_code == 303 and again.headers["Location"].endswith("/audit")
    assert client.post(f"/audit/{first}", data={"verdict": "does_not_hold"}).status_code == 409
    assert len(log.entries(board_engine, concept, kind="audit")) == 1


def test_a_skip_for_a_paper_that_has_a_verdict_is_not_written(board_engine, concept) -> None:
    app, _ = make(board_engine, concept, n=2)
    client = app.test_client()
    first = order_of(concept, 2)[0]
    client.post(f"/audit/{first}", data={"verdict": "holds"})
    response = client.post(f"/audit/{first}", data={"verdict": "skip"})
    assert response.status_code == 303 and response.headers["Location"].endswith("/audit")
    assert [e.verdict for e in log.entries(board_engine, concept, kind="audit")] == ["holds"]


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


def test_a_request_for_another_host_is_403_whatever_the_method(board_engine, concept) -> None:
    app, _ = make(board_engine, concept, n=2)
    client = app.test_client()
    first = order_of(concept, 2)[0]
    for host in ("evil.example", "evil.example:5000", "localhost.evil.example", "127.0.0.2"):
        assert client.get("/audit", headers={"Host": host}).status_code == 403
        post = client.post(f"/audit/{first}", data={"verdict": "holds"}, headers={"Host": host})
        assert post.status_code == 403
    assert log.entries(board_engine, concept, kind="audit") == []
    for host in ("localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000", "LOCALHOST"):
        assert client.get("/audit", headers={"Host": host}).status_code == 200


# -- the concept card and the paper note ---------------------------------------------------

ANSWER = "Stop when the interval is narrower than the stakes allow"


def blind_step(client, concept, n, verdicts=None):
    """The owner's blind verdicts (a dict by paper number); holds for any not named."""
    verdicts = verdicts or {}
    give_all(client, concept, {doc(i): verdicts.get(i, "holds") for i in range(n)})


def sections(html: str) -> dict[str, str]:
    """The card's parts by heading, so a test can say which part a paper is in."""
    parts = html.split("<h2>")[1:]
    return {part.split("</h2>")[0]: part for part in parts}


def test_the_card_and_the_note_are_locked_until_every_paper_has_a_verdict(
    board_engine, concept
) -> None:
    app, papers = make(board_engine, concept, n=3, details={0: {"answer": ANSWER}})
    client = app.test_client()
    first = order_of(concept, 3)[0]
    client.post(f"/audit/{first}", data={"verdict": "holds"})
    for url in ("/concept", f"/paper/{first}", f"/paper/{doc(99)}"):
        html = page(client, url, status=423)
        assert 'href="/audit"' in html and "blind audit" in html.lower()
        for secret in (RATIONALE, "reader-model-9", "first-read/v1", ANSWER, "<mark"):
            assert secret not in html
        assert QUOTE not in html and "Title of paper" not in html
    blind_step(client, concept, 3)
    assert client.get("/concept").status_code == 200
    assert client.get(f"/paper/{first}").status_code == 200


def test_the_card_is_locked_when_there_is_nothing_to_audit(board_engine, concept) -> None:
    app = create_app(board_engine, concept, {}, set())
    assert app.test_client().get("/concept").status_code == 423


def test_the_nav_leads_to_the_concept_card(board_engine, concept) -> None:
    app, _ = make(board_engine, concept, n=1)
    assert 'href="/concept"' in page(app.test_client(), "/audit")


def test_the_card_shows_the_wording_the_figures_and_each_answer_with_its_source(
    board_engine, concept
) -> None:
    app, papers = make(
        board_engine,
        concept,
        n=4,
        verdicts=["holds", "holds", "does_not_hold", "failed"],
        dates={0: "2025-03-09", 1: "2025-03-30", 2: "2024-05-05", 3: "2024-01-01"},
        details={0: {"answer": ANSWER}, 1: {"answer": "A second answer"}},
    )
    client = app.test_client()
    blind_step(client, concept, 4, {2: "does_not_hold", 3: "does_not_hold"})
    html = page(client, "/concept")
    for tag in (">paper<", ">model<", ">decided<"):
        assert tag in html
    assert WORDING in html and "version 1" in html and "owner" in html
    parts = sections(html)
    answers = parts["Answers"]
    assert ANSWER in answers and "A second answer" in answers
    assert answers.count(QUOTE) == 2
    assert f'href="/paper/{doc(0)}"' in answers and "Title of paper 0" in answers
    assert "2025-03" in answers
    assert "Title of paper 2" not in answers and "Title of paper 3" not in answers
    assert "reader-model-9" in html and "first-read/v1" in html
    counts = parts["Counts"]
    for line in (
        "Papers matching the words: 4",
        "Held back: 0",
        "Read: 4",
        "Reader says holds: 2",
        "Reader says does not hold: 1",
        "Failed readings: 1",
        "3 of 3",  # agreement: the failed reading is not compared
        "2 of 4",  # precision: two papers hold after the decisions
    ):
        assert line in counts, line
    assert "95% interval" in counts
    assert "2025-03: 2 papers" in parts["The field's history"]


def test_the_card_says_when_the_agreement_cannot_be_worked_out(board_engine, concept) -> None:
    app, _ = make(board_engine, concept, n=2, verdicts=["failed", "failed"])
    client = app.test_client()
    blind_step(client, concept, 2)
    counts = sections(page(client, "/concept"))["Counts"]
    assert "not yet available" in counts
    assert "Failed readings: 2" in counts


def test_a_rejected_paper_is_listed_under_other_meanings_with_its_reason(
    board_engine, concept
) -> None:
    app, papers = make(
        board_engine,
        concept,
        n=3,
        verdicts=["holds", "does_not_hold", "failed"],
        details={0: {"answer": ANSWER}},
    )
    client = app.test_client()
    blind_step(client, concept, 3, {1: "does_not_hold", 2: "does_not_hold"})
    parts = sections(page(client, "/concept"))
    other = parts["Other meanings of the words"]
    assert "Title of paper 1" in other and RATIONALE in other
    assert "Title of paper 2" in other  # the reading failed: no reason to show
    assert "Title of paper 0" not in other
    assert "Title of paper 1" not in parts["Answers"]


def test_a_decision_that_overturned_the_blind_verdict_lists_the_paper_by_its_final_verdict(
    board_engine, concept
) -> None:
    app, papers = make(
        board_engine,
        concept,
        n=3,
        verdicts=["holds", "does_not_hold", "does_not_hold"],
        details={0: {"answer": ANSWER}},
    )
    client = app.test_client()
    # reader / owner blind: 0 holds / no; 1 no / holds; 2 no / holds
    blind_step(client, concept, 3, {0: "does_not_hold"})
    for i, verdict in ((0, "holds"), (1, "does_not_hold"), (2, "holds")):
        reply = client.post(
            f"/audit/disagreements/{doc(i)}", data={"verdict": verdict, "reason": f"because {i}"}
        )
        assert reply.status_code == 303
    parts = sections(page(client, "/concept"))
    answers, other = parts["Answers"], parts["Other meanings of the words"]
    assert "Title of paper 0" in answers and ANSWER in answers
    assert "Title of paper 0" not in other
    assert "Title of paper 1" in other and "Title of paper 1" not in answers
    # the reader said does not hold and the owner's final verdict is holds: no answer shown
    assert "Title of paper 2" in answers
    row = answers.split("Title of paper 2")[1].split("</li>")[0]
    assert "reader said it does not hold" in row.lower() and ANSWER not in row
    assert ANSWER in answers.split("Title of paper 2")[0]


def test_nothing_of_a_held_back_paper_is_on_the_card_or_reachable(board_engine, concept) -> None:
    secret = "okapi passage only the held back paper has"
    app, papers = make(
        board_engine,
        concept,
        n=3,
        held_back=(2,),
        texts={2: f"A {QUOTE} with an {secret}."},
        details={2: {"answer": "secret answer"}},
    )
    client = app.test_client()
    blind_step(client, concept, 2)
    html = page(client, "/concept")
    for hidden in ("Title of paper 2", doc(2), "secret answer", secret):
        assert hidden not in html
    assert "Papers matching the words: 3" in html and "Held back: 1" in html
    assert "Read: 2" in html
    assert client.get(f"/paper/{doc(2)}").status_code == 404


def test_the_paper_note_shows_the_abstract_marked_and_the_whole_story(
    board_engine, concept
) -> None:
    hostile = f"<b>bold</b> & so on: it sets a {QUOTE} for <script>alert(1)</script> its review."
    app, papers = make(
        board_engine,
        concept,
        n=2,
        texts={0: hostile, 1: hostile},
        details={0: {"answer": ANSWER}, 1: {"answer": ANSWER}},
    )
    client = app.test_client()
    first = finish_with_one_disagreement(client, papers, concept, 2)
    client.post(f"/audit/disagreements/{first}", data={"verdict": "holds", "reason": "it does"})
    p = papers[first]
    html = page(client, f"/paper/{first}")
    assert p.title in html and p.work_id in html and p.publication_date in html
    assert f"<mark>{QUOTE}</mark>" in html
    assert "&lt;b&gt;bold&lt;/b&gt; &amp; so on: it sets a <mark>" in html
    assert "&lt;script&gt;" in html and "<script>" not in html
    for shown in (ANSWER, RATIONALE, "reader-model-9", "first-read/v1", "it does"):
        assert shown in html
    assert "does not hold" in html.lower()  # the owner's blind verdict
    assert ">paper<" in html and ">model<" in html and ">decided<" in html


def test_the_paper_note_without_a_decision_has_none(board_engine, concept) -> None:
    app, papers = make(board_engine, concept, n=1)
    client = app.test_client()
    blind_step(client, concept, 1)
    html = page(client, f"/paper/{doc(0)}")
    assert "No decision" in html
    assert page(client, f"/paper/{doc(7)}", status=404)


def test_the_note_of_a_failed_reading_shows_the_stop_reason_and_the_unverified_quote(
    board_engine, concept
) -> None:
    detail = {"stop_reason": "quote_not_verbatim", "model_quote": "words the abstract lacks"}
    app, _ = make(board_engine, concept, n=1, verdicts=["failed"], details={0: detail})
    client = app.test_client()
    blind_step(client, concept, 1)
    html = page(client, f"/paper/{doc(0)}")
    assert "quote_not_verbatim" in html
    assert "words the abstract lacks" in html and "not verbatim" in html.lower()
    assert "<mark" not in html


def test_our_history_lists_every_entry_in_order_in_pacific_time(board_engine, concept) -> None:
    app, papers = make(board_engine, concept, n=2, held_back=(), verdicts=["holds", "holds"])
    client = app.test_client()
    first = finish_with_one_disagreement(client, papers, concept, 2)
    client.post(f"/audit/disagreements/{first}", data={"verdict": "holds", "reason": "stops here"})
    history = sections(page(client, "/concept"))["Our history"]
    kinds = re.findall(r"<b>(\w+)</b>", history)
    assert kinds[0] == "concept" and kinds[-1] == "decision"
    assert sorted(kinds) == sorted(["concept"] + ["reading"] * 2 + ["audit"] * 2 + ["decision"])
    assert kinds.index("reading") < kinds.index("audit")  # the order they were written
    assert history.count("<li") == 1 + 2 + 2 + 1
    assert "stops here" in history and "reader-model-9" in history
    assert "PST" in history or "PDT" in history


def test_our_history_leaves_out_a_held_back_paper(board_engine, concept) -> None:
    app, papers = make(board_engine, concept, n=3, held_back=(2,))
    client = app.test_client()
    blind_step(client, concept, 2)
    history = sections(page(client, "/concept"))["Our history"]
    assert history.count("<li") == 1 + 2 + 2
    assert "Title of paper 2" not in history
