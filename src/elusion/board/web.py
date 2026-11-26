"""The audit pages: a small web app the owner runs on his own machine.

Flask is a Python library for writing web apps. You map a URL to a function, and the
function returns a page. The pages are *server-rendered*: each is built on the server as
plain HTML from a template and sent whole, with no JavaScript and no build step. Each
button is an ordinary HTML form that POSTs to the server, which writes one row to the
append-only `concept_log` and sends the browser on to the next page.

Nothing is kept in the app between requests. Every page is computed from the log by
`audit.py`, so restarting the app, or opening it in two tabs, cannot make a page disagree
with the log. The app listens on 127.0.0.1 only, and refuses a POST that another site's
page sent (its `Origin` header is not this page's own), so a web page elsewhere cannot
make the owner's browser file verdicts here.

Blindness is the point of the design. Until the owner has a verdict on every paper, the
only pages that can be reached show the concept and an abstract, and a count. The reader's
verdicts, quotes and reasons are not read by those routes at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import Flask, abort, redirect, render_template, request, url_for
from markupsafe import Markup, escape
from sqlalchemy.engine import Engine

from elusion.board import audit, log
from elusion.board.batch import Paper
from elusion.board.quotes import find_quote

_WORDS = {"holds": "Holds", "does_not_hold": "Does not hold", "skip": "Skip", "failed": "Failed"}


@dataclass(frozen=True)
class _State:
    """What the log says right now about the papers being audited."""

    version: int
    wording: str
    doc_ids: list[str]
    verdicts: int  # papers with the owner's verdict
    held_back_n: int  # papers that have a reading but are kept out of the audit

    @property
    def finished(self) -> bool:
        return bool(self.doc_ids) and self.verdicts == len(self.doc_ids)


def marked(text: str, quote: str | None) -> Markup:
    """`text` as safe HTML with `quote` wrapped in <mark> where it appears. Everything is
    escaped, the abstract and the quote alike."""
    span = find_quote(text, quote) if quote else None
    if span is None:
        return Markup(escape(text))
    start, end = span
    return Markup("{}<mark>{}</mark>{}").format(text[:start], text[start:end], text[end:])


def create_app(
    engine: Engine, concept: str, papers: dict[str, Paper], held_back: set[str]
) -> Flask:
    """The audit app for `concept`. It serves the papers that have a reading under the
    concept's current version, leaving out `held_back` ones, which it treats as unknown."""
    app = Flask(__name__)
    app.jinja_env.filters["words"] = lambda verdict: _WORDS[verdict]
    app.jinja_env.filters["pct"] = lambda share: f"{share * 100:.1f}%"

    def state() -> _State:
        entry = log.current_concept(engine, concept)
        version = entry.concept_version
        read = {e.doc_id for e in log.entries(engine, concept, version, kind="reading")}
        doc_ids = sorted(d for d in papers if d in read and d not in held_back)
        verdicts = len(audit.blind_verdicts(engine, concept, version, doc_ids))
        held_back_n = len(read & held_back & papers.keys())
        return _State(version, entry.body or "", doc_ids, verdicts, held_back_n)

    @app.before_request
    def same_origin_only():
        origin = request.headers.get("Origin")
        if request.method == "POST" and origin is not None:
            if origin != f"{request.scheme}://{request.host}":
                abort(403)

    @app.get("/")
    def home():
        return redirect(url_for("audit_page"))

    @app.get("/audit")
    def audit_page():
        s = state()
        doc_id = audit.next_blind(engine, concept, s.version, s.doc_ids)
        if doc_id is None:
            return redirect(url_for("disagreements_page"))
        return render_template(
            "audit.html",
            wording=s.wording,
            paper=papers[doc_id],
            k=s.verdicts + 1,
            n=len(s.doc_ids),
        )

    @app.post("/audit/<doc_id>")
    def give_verdict(doc_id: str):
        s = state()
        if doc_id not in s.doc_ids:
            abort(404)
        verdict = request.form.get("verdict")
        if verdict not in ("holds", "does_not_hold", "skip"):
            abort(400)
        try:
            log.add_audit(engine, concept, s.version, doc_id, verdict)
        except log.AlreadyAudited:
            abort(409)
        return redirect(url_for("audit_page"), 303)

    def unfinished(s: _State):
        return render_template("wait.html", remaining=len(s.doc_ids) - s.verdicts), 409

    @app.get("/audit/disagreements")
    def disagreements_page():
        s = state()
        if not s.finished:
            return unfinished(s)
        reader = audit.reader_of(engine, concept, s.version)
        pending = [
            d
            for d in audit.disagreements(engine, concept, s.version, reader, s.doc_ids)
            if d.decision is None
        ]
        if not pending:
            return redirect(url_for("summary_page"))
        d = pending[0]
        paper = papers[d.doc_id]
        return render_template(
            "disagreement.html",
            wording=s.wording,
            d=d,
            paper=paper,
            abstract=marked(paper.text, d.reading.quote),
            left=len(pending),
        )

    @app.post("/audit/disagreements/<doc_id>")
    def settle(doc_id: str):
        s = state()
        if not s.finished:
            abort(409)
        reader = audit.reader_of(engine, concept, s.version)
        found = {
            d.doc_id: d for d in audit.disagreements(engine, concept, s.version, reader, s.doc_ids)
        }
        if doc_id not in found:
            abort(404)
        verdict = request.form.get("verdict")
        reason = request.form.get("reason", "").strip()
        if verdict not in ("holds", "does_not_hold") or not reason:
            abort(400)
        d = found[doc_id]
        log.add_decision(
            engine,
            concept,
            s.version,
            reason=reason,
            actor="owner",
            doc_id=doc_id,
            verdict=verdict,
            rests_on=[d.blind.entry_id, d.reading.entry_id],
        )
        return redirect(url_for("disagreements_page"), 303)

    @app.get("/audit/summary")
    def summary_page():
        s = state()
        if not s.finished:
            return render_template(
                "summary.html", finished=False, audited=s.verdicts, n=len(s.doc_ids)
            )
        reader = audit.reader_of(engine, concept, s.version)
        figures = audit.summary(engine, concept, s.version, reader, s.doc_ids, s.held_back_n)
        return render_template("summary.html", finished=True, s=figures)

    return app
