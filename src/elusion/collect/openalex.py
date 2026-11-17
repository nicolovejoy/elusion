"""OpenAlex: the client that pages through a search, and one work turned into one document.

OpenAlex (https://openalex.org) is an open catalogue of scholarly works with an HTTP API.
The facts this module relies on were read from its documentation and checked against the
live API on 2026-11-16; they are in docs/plans/phase-7-literature.md under task 0.

Three things here are worth knowing.

*The abstract comes as an inverted index.* OpenAlex does not serve an abstract as text.
It serves a map from each word to the positions where the word occurs, and
`rebuild_abstract` puts the words back in order.

*Paging is by cursor.* A search returns 100 works a page. Each response carries a
`next_cursor`, an opaque token for the following page, and the last page carries none.
Unlike page numbers, a cursor has no 10,000-result ceiling.

*The API is metered.* A key carries a free daily allowance; a search page costs $0.001.
Over a limit the API answers `429` with a `Retry-After` header, and the client waits that
long and asks again. The key travels in a header, never in the URL, so it cannot end up
in a log line that prints a URL.

A document's id is derived from the work's id and a hash of its text. If OpenAlex later
serves a different abstract for the same work, that is a different id, so a new
document, and the one already reviewed stays exactly as it was reviewed.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterator
from typing import TypedDict

import httpx

from elusion.collect.terms import OPENALEX_CORPUS, Term

BASE_URL = "https://api.openalex.org"
PER_PAGE = 100  # the most the API allows
SELECT = (
    "id,doi,title,publication_year,publication_date,language,type,updated_date,"
    "abstract_inverted_index"
)


class OpenAlexError(RuntimeError):
    """OpenAlex refused a request, or kept failing after the retries."""


class Work(TypedDict):
    doc_id: str  # 16 hex characters, from the work id and the content hash
    work_id: str  # OpenAlex's id, such as W2029075138
    title: str  # empty when OpenAlex has none
    year: int  # publication year
    publication_date: str | None
    language: str | None  # detected by OpenAlex, not declared by the publisher
    type: str | None
    doi: str | None
    content_hash: str  # SHA-256 of `text`
    text: str  # what a reviewer reads: the title, a blank line, the abstract


def rebuild_abstract(index: dict[str, list[int]] | None) -> str:
    """The abstract as text, from OpenAlex's word -> positions map; "" when there is none."""
    if not index:
        return ""
    placed = sorted((position, word) for word, positions in index.items() for position in positions)
    return " ".join(word for _, word in placed)


def document_text(title: str | None, abstract: str) -> str:
    """Title, a blank line, abstract. Runs of whitespace in the title become one space."""
    title = " ".join((title or "").split())
    return f"{title}\n\n{abstract}" if title else abstract


def abstract_doc_id(work_id: str, content_hash: str) -> str:
    """Stable 16-hex id for one version of one work's text.

    The same shape as a 10-K paragraph's id, so every table and file keyed by `doc_id`
    takes an abstract unchanged. The `openalex` prefix keeps the two id spaces apart.
    """
    return hashlib.sha256(f"openalex\x1f{work_id}\x1f{content_hash}".encode()).hexdigest()[:16]


def to_work(raw: dict) -> Work | None:
    """One work of an API response as a document; None when it has no abstract or no year."""
    abstract = rebuild_abstract(raw.get("abstract_inverted_index"))
    year = raw.get("publication_year")
    if not abstract or year is None:
        return None
    work_id = raw["id"].rsplit("/", 1)[-1]  # the API gives https://openalex.org/W...
    text = document_text(raw.get("title"), abstract)
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    return {
        "doc_id": abstract_doc_id(work_id, content_hash),
        "work_id": work_id,
        "title": " ".join((raw.get("title") or "").split()),
        "year": int(year),
        "publication_date": raw.get("publication_date"),
        "language": raw.get("language"),
        "type": raw.get("type"),
        "doi": raw.get("doi"),
        "content_hash": content_hash,
        "text": text,
    }


def search_filter(from_date: str) -> str:
    """The filter every harvest query carries; in OpenAlex a comma between filters is AND."""
    return f"from_publication_date:{from_date},has_abstract:true"


def citing_filter(anchor_id: str, from_date: str) -> str:
    """The filter for the works that cite `anchor_id`, in the same window as a search."""
    return f"cites:{anchor_id},{search_filter(from_date)}"


class OpenAlex:
    """A small client for the works endpoint. `pages` counts the requests answered."""

    def __init__(
        self,
        api_key: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 6,
    ) -> None:
        if not api_key:
            raise OpenAlexError(
                "OPENALEX_API_KEY is not set: OpenAlex refuses the second page of a search "
                "without a key. A key is free at https://openalex.org/settings/api"
            )
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
            transport=transport,
        )
        self._sleep = sleep
        self._max_attempts = max_attempts
        self.pages = 0
        self.remaining_usd: str | None = None  # today's allowance left, as last reported

    def search(self, term: Term, from_date: str, max_pages: int | None = None) -> Iterator[dict]:
        """Every work matching `term` in title or abstract, published since `from_date`
        and having an abstract, as the API serves it. `max_pages` stops early (a smoke run).
        """
        parameter = (
            "search.title_and_abstract.exact"
            if term.form == "exact"
            else "search.title_and_abstract"
        )
        params = {
            parameter: term.query,
            "filter": search_filter(from_date),
            "corpus": OPENALEX_CORPUS,
            "per_page": str(PER_PAGE),
            "select": SELECT,
        }
        cursor: str | None = "*"  # the documented value for "the first page"
        asked = 0
        while cursor and (max_pages is None or asked < max_pages):
            body = self._get("/works", {**params, "cursor": cursor})
            asked += 1
            yield from body["results"]
            cursor = body["meta"].get("next_cursor") if body["results"] else None

    def citing_sample(self, anchor_id: str, from_date: str, n: int, seed: int) -> list[dict]:
        """A seeded random sample of `n` works that cite `anchor_id`, published since
        `from_date` and having an abstract; all of them when there are fewer than `n`.

        The same seed returns the same works while the catalogue is unchanged (checked
        2026-11-16). OpenAlex does not promise it afterwards, so the custody rows written
        from this answer are the record of what was drawn.
        """
        if n > PER_PAGE:
            raise ValueError(f"a sample is one page: at most {PER_PAGE} works, not {n}")
        body = self._get(
            "/works",
            {
                "filter": citing_filter(anchor_id, from_date),
                "sample": str(n),
                "seed": str(seed),
                "corpus": OPENALEX_CORPUS,
                "per_page": str(PER_PAGE),
                "select": SELECT,
            },
        )
        return body["results"]

    def _get(self, path: str, params: dict[str, str]) -> dict:
        problem = "no attempt made"
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._http.get(path, params=params)
            except httpx.TransportError as e:  # a dropped connection, a timeout
                problem = f"{type(e).__name__}: {e}"
            else:
                if response.status_code == 200:
                    self.pages += 1
                    self.remaining_usd = response.headers.get("x-ratelimit-remaining-usd")
                    return response.json()
                problem = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code == 429:
                    self._sleep(int(response.headers.get("Retry-After", "60")) + 1)
                    continue
                if response.status_code < 500:  # the request itself is wrong: do not repeat it
                    raise OpenAlexError(f"OpenAlex refused the request. {problem}")
            if attempt < self._max_attempts:
                self._sleep(min(2**attempt, 60))
        raise OpenAlexError(f"OpenAlex failed {self._max_attempts} times running. {problem}")
