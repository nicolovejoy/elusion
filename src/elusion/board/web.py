"""The board's pages: a small web app the owner runs on his own machine.

Flask is a Python library for writing web apps. You map a URL to a function, and the
function returns a page. The pages are *server-rendered*: each is built on the server as
plain HTML from a template and sent whole, with no JavaScript and no build step. Each
button is an ordinary HTML form that POSTs to the server, which writes one row to the
append-only `concept_log` and sends the browser on to the next page.

Nothing is kept in the app between requests. Every page is computed from the log by
`audit.py`, so restarting the app, or opening it in two tabs, cannot make a page disagree
with the log. The app listens on 127.0.0.1 only. It answers only requests addressed to
`localhost`, `127.0.0.1` or `[::1]`: another site can make a browser send its own name to
this address (*DNS rebinding*), and a page that thinks it is that site could then read
these pages. It also refuses a POST that another site's page sent (its `Origin` header is
not this page's own), so a web page elsewhere cannot make the owner's browser file
verdicts here.

Blindness is the point of the design. Until the owner has a verdict on every paper, the
only pages that can be reached show the concept and an abstract, and a count. Two of them
let him go back to papers he has already seen (`/audit/seen`), to flag them or to settle a
skip; they read only his own audit and flag entries, never the reading. The reader's
verdicts, quotes and reasons are not read by those routes at all. The concept card and the
paper notes, which show them, answer "locked" until that step is done. A held-back paper
(one kept for evaluation) never appears on any page.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlsplit

from flask import Flask, abort, redirect, render_template, request, url_for
from sqlalchemy.engine import Engine

from elusion.board import audit, card, log
from elusion.board.batch import Paper
from elusion.board.log import Entry

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_NOTE_MAX = 1000  # characters in a flag's note
_WORDS = {"holds": "Holds", "does_not_hold": "Does not hold", "skip": "Skip", "failed": "Failed"}


@dataclass
class _Seen:
    """One paper the owner has already seen in the blind step."""

    paper: Paper
    verdict: str | None  # his holds / does_not_hold, or None when he has only skipped it
    flags: list[Entry]


@dataclass(frozen=True)
class _State:
    """What the log says right now about the papers being audited."""

    entry: Entry  # the current wording of the concept
    version: int
    wording: str
    doc_ids: list[str]
    verdicts: int  # papers with the owner's verdict
    held_back_n: int  # papers that have a reading but are kept out of the audit

    @property
    def finished(self) -> bool:
        return bool(self.doc_ids) and self.verdicts == len(self.doc_ids)


def create_app(
    engine: Engine, concept: str, papers: dict[str, Paper], held_back: set[str]
) -> Flask:
    """The audit app for `concept`. It serves the papers that have a reading under the
    concept's current version, leaving out `held_back` ones, which it treats as unknown."""
    app = Flask(__name__)
    app.jinja_env.filters["words"] = lambda verdict: _WORDS[verdict]
    app.jinja_env.filters["pct"] = lambda share: f"{share * 100:.1f}%"
    app.jinja_env.filters["when"] = card.display_time
    app.jinja_env.filters["month"] = card.field_month

    def state() -> _State:
        entry = log.current_concept(engine, concept)
        version = entry.concept_version
        read = {e.doc_id for e in log.entries(engine, concept, version, kind="reading")}
        doc_ids = sorted(d for d in papers if d in read and d not in held_back)
        verdicts = len(audit.blind_verdicts(engine, concept, version, doc_ids))
        held_back_n = len(read & held_back & papers.keys())
        return _State(entry, version, entry.body or "", doc_ids, verdicts, held_back_n)

    @app.before_request
    def local_requests_only():
        if urlsplit(f"//{request.host}").hostname not in _LOCAL_HOSTS:
            abort(403)
        origin = request.headers.get("Origin")
        if request.method == "POST" and origin is not None:
            if origin != f"{request.scheme}://{request.host}":
                abort(403)

    def seen_papers(s: _State) -> list[_Seen]:
        """The papers the owner has an audit entry for (a verdict or a skip), in the order he
        first saw them, each with his own outcome and his flags. This reads only the `audit`
        and `flag` kinds of the log: nothing of any reading, so what these pages show cannot
        depend on it."""
        flags: dict[str, list[Entry]] = {}
        for e in log.entries(engine, concept, kind="flag"):
            flags.setdefault(e.doc_id, []).append(e)
        found: dict[str, _Seen] = {}
        for e in log.entries(engine, concept, s.version, kind="audit"):
            if e.doc_id not in s.doc_ids:
                continue
            seen = found.setdefault(
                e.doc_id, _Seen(papers[e.doc_id], None, flags.get(e.doc_id, []))
            )
            if e.verdict in ("holds", "does_not_hold"):
                seen.verdict = e.verdict
        return list(found.values())

    def add_flag(s: _State, doc_id: str) -> None:
        """Write the owner's flag on `doc_id`, with the note typed beside it (may be blank)."""
        note = request.form.get("note", "").strip()
        if len(note) > _NOTE_MAX:
            abort(400)
        log.add_flag(
            engine, concept=concept, version=s.version, doc_id=doc_id, actor="owner", note=note
        )

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
            seen_n=len(seen_papers(s)),
        )

    @app.get("/audit/seen")
    def seen_page():
        s = state()
        seen = seen_papers(s)
        skipped = [x for x in seen if x.verdict is None]
        return render_template(
            "seen.html",
            skipped=skipped,
            decided=[x for x in seen if x.verdict is not None],
            unseen=len(s.doc_ids) - len(seen),
        )

    def seen_one(s: _State, doc_id: str) -> _Seen:
        """The seen paper `doc_id`, or a 404: unknown, held back, or not yet shown."""
        for x in seen_papers(s):
            if x.paper.doc_id == doc_id:
                return x
        abort(404)

    @app.get("/audit/seen/<doc_id>")
    def seen_paper_page(doc_id: str):
        s = state()
        return render_template("seen_paper.html", wording=s.wording, x=seen_one(s, doc_id))

    @app.post("/audit/seen/<doc_id>/flag")
    def flag_seen(doc_id: str):
        s = state()
        seen_one(s, doc_id)
        add_flag(s, doc_id)
        return redirect(url_for("seen_paper_page", doc_id=doc_id), 303)

    @app.post("/audit/<doc_id>")
    def give_verdict(doc_id: str):
        s = state()
        if doc_id not in s.doc_ids:
            abort(404)
        verdict = request.form.get("verdict")
        if verdict not in ("holds", "does_not_hold", "skip"):
            abort(400)

        def stored():
            return audit.blind_verdicts(engine, concept, s.version, [doc_id]).get(doc_id)

        given = stored()
        if given is None:
            try:
                log.add_audit(engine, concept, s.version, doc_id, verdict)
            except log.AlreadyAudited:  # two clicks at once: the other one won
                given = stored()
            else:
                if request.form.get("flag"):
                    add_flag(s, doc_id)
                return redirect(url_for("audit_page"), 303)
        # The paper already has a verdict. The same one again is a double click or a resubmit,
        # and a skip adds nothing to a paper that has been decided; a different one is refused.
        if given is None or verdict not in ("skip", given.verdict):
            abort(409)
        return redirect(url_for("audit_page"), 303)

    def unfinished(s: _State):
        return render_template("wait.html", remaining=len(s.doc_ids) - s.verdicts), 409

    def locked(s: _State):
        return render_template("locked.html", remaining=len(s.doc_ids) - s.verdicts), 423

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
            abstract=card.mark_quotes(paper.text, [d.reading.quote or ""]),
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
        if d.decision is not None:
            # Already settled: a double click or a resubmit. The log is append-only, so a
            # second row would stay for good.
            return redirect(url_for("disagreements_page"), 303)
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

    def flagged_papers(s: _State) -> list[tuple[Paper, list[str]]]:
        """Each paper the owner flagged, once, with its notes, in the order it was first
        flagged. Only papers the page serves: a held-back paper is not among them."""
        found: dict[str, list[str]] = {}
        for e in log.entries(engine, concept, kind="flag"):
            if e.doc_id in s.doc_ids:
                notes = found.setdefault(e.doc_id, [])
                if e.body:
                    notes.append(e.body)
        return [(papers[d], notes) for d, notes in found.items()]

    @app.get("/concept")
    def card_page():
        s = state()
        if not s.finished:
            return locked(s)
        reader = audit.reader_of(engine, concept, s.version)
        figures = audit.summary(engine, concept, s.version, reader, s.doc_ids, s.held_back_n)
        readings = audit.reader_readings(engine, concept, s.version, reader, s.doc_ids)
        final = audit.final_verdicts(engine, concept, s.version, s.doc_ids)

        def rows(verdict: str):
            """Papers whose final verdict is `verdict`, as (paper, its reading), by date."""
            wanted = [papers[d] for d in s.doc_ids if final[d] == verdict]
            wanted.sort(key=lambda p: (p.publication_date, p.doc_id))
            return [(p, readings[p.doc_id]) for p in wanted]

        holds = rows("holds")
        by_reader = Counter(r.verdict for r in readings.values())
        return render_template(
            "card.html",
            s=s,
            reader=reader,
            figures=figures,
            matching=len(s.doc_ids) + s.held_back_n,
            by_reader=by_reader,
            answers=holds,
            flagged=flagged_papers(s),
            others=rows("does_not_hold"),
            history=card.field_history(p for p, _ in holds),
            log=[
                (e, papers.get(e.doc_id))
                for e in log.entries(engine, concept)
                if e.doc_id not in held_back
            ],
        )

    @app.get("/paper/<doc_id>")
    def paper_page(doc_id: str):
        s = state()
        if not s.finished:
            return locked(s)
        if doc_id not in s.doc_ids:
            abort(404)
        reader = audit.reader_of(engine, concept, s.version)
        ids = [doc_id]
        reading = audit.reader_readings(engine, concept, s.version, reader, ids)[doc_id]
        paper = papers[doc_id]
        return render_template(
            "paper.html",
            paper=paper,
            reading=reading,
            abstract=card.mark_quotes(paper.text, [reading.quote or ""]),
            blind=audit.blind_verdicts(engine, concept, s.version, ids)[doc_id],
            decision=audit.latest_decisions(engine, concept, s.version, ids).get(doc_id),
            flags=[e for e in log.entries(engine, concept, kind="flag") if e.doc_id == doc_id],
        )

    @app.post("/paper/<doc_id>/flag")
    def flag_paper(doc_id: str):
        s = state()
        if not s.finished:
            return locked(s)
        if doc_id not in s.doc_ids:
            abort(404)
        add_flag(s, doc_id)
        return redirect(url_for("paper_page", doc_id=doc_id), 303)

    return app
