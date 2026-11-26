"""The audit's logic: what the owner judged, where he and the reader differ, and the totals.

An *audit* here is a check of the model's readings by the owner. It is done *blind*: he
reads one abstract and gives his own verdict without seeing the model's, so the model's
answer cannot lean on his. Only after he has a verdict on every paper does he see the
disagreements and settle each one.

Nothing in this module is stored or remembered between calls. Every function asks the
append-only `concept_log` what has been written and works the answer out afresh, so the
log is the only truth and a page can never disagree with it. A button on a page is one
`insert` into the log, and what the owner sees next is computed from the rows.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.engine import Engine

from elusion.board import log
from elusion.board.log import Entry
from elusion.evaluate.metrics import Estimate, proportion

VERDICTS = ("holds", "does_not_hold")


@dataclass(frozen=True)
class ReaderKey:
    """Whose readings the audit compares against: one model under one prompt version."""

    model: str
    prompt_version: str


@dataclass(frozen=True)
class Disagreement:
    doc_id: str
    blind: Entry  # the owner's verdict, given before he saw the reading
    reading: Entry  # the reader's answer
    decision: Entry | None  # the owner's latest ruling on it, once he has made one


@dataclass(frozen=True)
class Summary:
    audited: int  # papers with a blind verdict
    compared: int  # papers with a blind verdict and a reading that did not fail
    agreed: int  # of those, the ones where the reader and the owner said the same
    agreement: Estimate | None  # None when nothing could be compared
    holds_final: int  # papers that hold the concept after the owner's decisions
    precision: Estimate | None  # holds_final out of audited: how good the word search is
    failed_readings: int
    unsettled: int  # disagreements with no decision yet
    held_back: int


def reader_of(engine: Engine, concept: str, version: int) -> ReaderKey:
    """The one (model, prompt version) pair that read this version of the concept."""
    pairs = {
        ReaderKey(e.model, e.prompt_version)
        for e in log.entries(engine, concept, version, kind="reading")
    }
    if not pairs:
        raise ValueError(f"no readings of {concept} v{version} yet")
    if len(pairs) > 1:
        pairs_text = sorted((p.model, p.prompt_version) for p in pairs)
        raise ValueError(f"more than one reader has read {concept} v{version}: {pairs_text}")
    return pairs.pop()


def audit_order(doc_ids: Sequence[str], concept: str, version: int) -> list[str]:
    """The order papers are shown in: a shuffle that is the same every time for one concept
    version, so a restart of the page picks up where it stopped."""
    ordered = sorted(doc_ids)
    random.Random(f"{concept}:{version}").shuffle(ordered)
    return ordered


def _audits(engine: Engine, concept: str, version: int, doc_ids: Sequence[str]) -> list[Entry]:
    wanted = set(doc_ids)
    return [e for e in log.entries(engine, concept, version, kind="audit") if e.doc_id in wanted]


def blind_verdicts(
    engine: Engine, concept: str, version: int, doc_ids: Sequence[str] | None = None
) -> dict[str, Entry]:
    """Per paper, the owner's one holds / does_not_hold entry. Skips are not verdicts."""
    entries = log.entries(engine, concept, version, kind="audit")
    wanted = None if doc_ids is None else set(doc_ids)
    return {
        e.doc_id: e
        for e in entries
        if e.verdict in VERDICTS and (wanted is None or e.doc_id in wanted)
    }


def next_blind(engine: Engine, concept: str, version: int, doc_ids: Sequence[str]) -> str | None:
    """The paper to show next. First, in `audit_order`, any with no audit entry at all.
    When every paper has one, the skipped paper without a verdict whose latest skip is
    oldest. None when every paper has a verdict."""
    audits = _audits(engine, concept, version, doc_ids)
    seen = {e.doc_id for e in audits}
    for doc_id in audit_order(doc_ids, concept, version):
        if doc_id not in seen:
            return doc_id
    given = {e.doc_id for e in audits if e.verdict in VERDICTS}
    latest_skip: dict[str, int] = {}
    for e in audits:  # entries come in the order written, so the last write wins
        if e.doc_id not in given:
            latest_skip[e.doc_id] = e.entry_id
    if not latest_skip:
        return None
    return min(latest_skip, key=latest_skip.__getitem__)


def latest_decisions(
    engine: Engine, concept: str, version: int, doc_ids: Sequence[str]
) -> dict[str, Entry]:
    """Per paper, the owner's latest decision that carries a verdict."""
    wanted = set(doc_ids)
    latest: dict[str, Entry] = {}
    for e in log.entries(engine, concept, version, kind="decision"):
        if e.doc_id in wanted and e.verdict in VERDICTS:
            latest[e.doc_id] = e
    return latest


def reader_readings(
    engine: Engine, concept: str, version: int, reader: ReaderKey, doc_ids: Sequence[str]
) -> dict[str, Entry]:
    """Per paper, the reading made by `reader`."""
    wanted = set(doc_ids)
    return {
        e.doc_id: e
        for e in log.entries(engine, concept, version, kind="reading")
        if e.doc_id in wanted
        and (e.model, e.prompt_version) == (reader.model, reader.prompt_version)
    }


def disagreements(
    engine: Engine, concept: str, version: int, reader: ReaderKey, doc_ids: Sequence[str]
) -> list[Disagreement]:
    """The papers where the reader's verdict differs from the owner's blind verdict, in
    audit order. A reading that failed is never a disagreement: it said nothing to differ."""
    blind = blind_verdicts(engine, concept, version, doc_ids)
    readings = reader_readings(engine, concept, version, reader, doc_ids)
    decisions = latest_decisions(engine, concept, version, doc_ids)
    found = []
    for doc_id in audit_order(doc_ids, concept, version):
        reading = readings.get(doc_id)
        if doc_id in blind and reading is not None and reading.verdict in VERDICTS:
            if reading.verdict != blind[doc_id].verdict:
                found.append(Disagreement(doc_id, blind[doc_id], reading, decisions.get(doc_id)))
    return found


def final_verdicts(
    engine: Engine, concept: str, version: int, doc_ids: Sequence[str]
) -> dict[str, str]:
    """Per audited paper, the owner's verdict as it stands: his latest decision where he has
    made one, else his blind verdict."""
    final = {d: e.verdict for d, e in blind_verdicts(engine, concept, version, doc_ids).items()}
    for doc_id, decision in latest_decisions(engine, concept, version, doc_ids).items():
        if doc_id in final:
            final[doc_id] = decision.verdict
    return final


def summary(
    engine: Engine,
    concept: str,
    version: int,
    reader: ReaderKey,
    doc_ids: Sequence[str],
    held_back_n: int,
) -> Summary:
    """The figures of the audit. `doc_ids` never include the held-back papers; `held_back_n`
    only says how many there are."""
    blind = blind_verdicts(engine, concept, version, doc_ids)
    readings = reader_readings(engine, concept, version, reader, doc_ids)
    final = final_verdicts(engine, concept, version, doc_ids)
    comparable = [d for d in blind if d in readings and readings[d].verdict in VERDICTS]
    agreed = sum(1 for d in comparable if readings[d].verdict == blind[d].verdict)
    holds_final = sum(1 for v in final.values() if v == "holds")
    pending = disagreements(engine, concept, version, reader, doc_ids)
    return Summary(
        audited=len(blind),
        compared=len(comparable),
        agreed=agreed,
        agreement=proportion(agreed, len(comparable)) if comparable else None,
        holds_final=holds_final,
        precision=proportion(holds_final, len(blind)) if blind else None,
        failed_readings=sum(1 for r in readings.values() if r.verdict == "failed"),
        unsettled=sum(1 for d in pending if d.decision is None),
        held_back=held_back_n,
    )
