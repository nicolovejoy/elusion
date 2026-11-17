"""What the harvest asks OpenAlex for: the search terms, the window and the catalogue.

This is the one committed place for them (docs/DECISIONS.md, 2026-11-16). A change
here changes the population the screen is measured on, so it is a decision, not a tweak.

A term is searched in one of two *forms*. OpenAlex *stems* a query by default: it cuts
each word back to its root, so "reviews" also finds "review". That is what the nine
original terms want. It is wrong for the agent terms, because the root of "agentic" is
"agent", which matches 766,639 works, and the root of "auditability" is "audit". Those
terms use the `exact` form, which matches the words as written, and so they spell out
each plural themselves.

Inside a query, a quoted phrase must appear as written, and upper-case AND and OR
combine phrases. Both forms search the title and the abstract.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    query: str  # the search string, as sent
    form: str  # "stemmed" or "exact"


FROM_DATE = "2025-01-01"  # works published on or after this day
OPENALEX_CORPUS = "core"  # OpenAlex's own word: its main catalogue, without the "expansion" set

AGENT_PHRASES = (
    "AI agent",
    "AI agents",
    "LLM agent",
    "LLM agents",
    "LLM-based agent",
    "LLM-based agents",
    "agentic AI",
    "agentic system",
    "agentic systems",
    "agentic workflow",
    "agentic workflows",
    "autonomous agent",
    "autonomous agents",
)


def _any(*words: str) -> str:
    """`(a OR "b c")`: a phrase of more than one word, or one with a hyphen, is quoted."""
    return "(" + " OR ".join(f'"{w}"' if " " in w or "-" in w else w for w in words) + ")"


_AGENT = _any(*AGENT_PHRASES)


def _agent_and(*words: str) -> Term:
    return Term(f"{_AGENT} AND {_any(*words)}", "exact")


TERMS: tuple[Term, ...] = (
    # The narrow core: validation of automated document review and citation screening.
    Term('"technology-assisted review"', "stemmed"),
    Term('"high-recall retrieval"', "stemmed"),
    Term('"citation screening"', "stemmed"),
    Term('"e-discovery" OR "electronic discovery"', "stemmed"),
    # The broad net.
    Term('"LLM-as-a-judge"', "stemmed"),
    Term('"algorithmic accountability"', "stemmed"),
    Term('"explainable AI" AND audit', "stemmed"),
    Term('"automated decision-making"', "stemmed"),
    Term('"audit trail"', "stemmed"),
    # Agents whose decisions can be audited: an agent phrase and a word from one group.
    _agent_and("audit", "audits", "auditing", "auditable", "auditability"),
    _agent_and("accountability", "accountable"),
    _agent_and("traceability", "traceable", "provenance"),
    _agent_and("human oversight", "human-in-the-loop"),
    _agent_and("logging", "observability", "audit log", "audit logs"),
    # Audits of AI by name, with or without an agent.
    Term(
        '"AI audit" OR "AI audits" OR "AI auditing" OR "algorithm audit" OR "algorithm audits" '
        'OR "algorithmic audit" OR "algorithmic audits" OR "algorithmic auditing"',
        "exact",
    ),
    Term('"decision provenance"', "exact"),
)
