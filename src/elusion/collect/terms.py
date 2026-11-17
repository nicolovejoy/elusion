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

The *anchors* are for a different question: how much on-topic work do the terms miss?
An anchor is a well-known paper, one for each strand of the topic. The harvest also
takes a random sample of the works that cite each anchor, found by the citation and not
by any search word. Those the screen and the judge call relevant are on-topic papers
known without the terms, and the fraction of them the terms also returned is the
harvest's recall. The sample is *seeded*: OpenAlex draws it from a number we supply, so
the same number draws the same works, and the draw can be checked.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    query: str  # the search string, as sent
    form: str  # "stemmed" or "exact"


@dataclass(frozen=True)
class Anchor:
    work_id: str  # OpenAlex's id for the anchor paper
    label: str  # authors and year, for logs and the report


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

# One per strand: e-discovery review, systematic-review screening, algorithmic auditing,
# LLM judges, and agents. Resolved to OpenAlex ids on 2026-11-16 (phase 7 spec, task 0).
ANCHORS: tuple[Anchor, ...] = (
    Anchor("W2029075138", "Cormack and Grossman 2014"),
    Anchor("W4313371821", "O'Mara-Eves and others 2015"),
    Anchor("W3001807593", "Raji and others 2020"),
    Anchor("W7133239970", "Zheng and others 2023"),
    Anchor("W4399364360", "Chan and others 2024"),
)
SEEDS_PER_ANCHOR = 75  # an anchor with fewer citing works in the window gives all it has
SAMPLE_SEED = 20261116  # the day the anchors were fixed; any number would do, once chosen
