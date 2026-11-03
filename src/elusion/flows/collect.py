"""Prefect flow `collect`: EDGAR-CORPUS -> paragraphs -> Parquet, Postgres, Kafka.

A Prefect *flow* is a Python function whose calls to *tasks* are tracked: each task
run gets state (pending, running, failed, completed), retries, and a row in the
server's UI. The code runs in this process; the server only records what happened.
Retries are on the download task because the Hub is the one flaky dependency.
"""

from __future__ import annotations

from pathlib import Path

from prefect import flow, get_run_logger, task

from elusion.collect import db, kafka
from elusion.collect.companies import load_company_names
from elusion.collect.edgar import iter_filings
from elusion.collect.parquet import Document, write_year, year_path
from elusion.collect.split import doc_id, split_paragraphs
from elusion.config import settings


@task(retries=3, retry_delay_seconds=30)
def download_and_split(year: int, limit: int | None, names: dict[str, str]) -> list[Document]:
    log = get_run_logger()
    docs: list[Document] = []
    seen: set[str] = set()
    filings = 0
    empty = 0
    duplicates = 0
    for filing in iter_filings(year, limit):
        filings += 1
        paragraphs = split_paragraphs(filing.risk_factors)
        if not paragraphs:
            empty += 1
        for i, text in enumerate(paragraphs):
            did = doc_id(filing.cik, filing.year, text)
            if did in seen:  # same paragraph repeated inside one filing
                duplicates += 1
                continue
            seen.add(did)
            docs.append(
                {
                    "doc_id": did,
                    "cik": filing.cik,
                    "company": names.get(filing.cik),
                    "year": filing.year,
                    "paragraph_index": i,
                    "text": text,
                }
            )
        if filings % 500 == 0:
            log.info("year=%s filings=%s paragraphs=%s", year, filings, len(docs))
    log.info(
        "year=%s done: filings=%s without Item 1A=%s paragraphs=%s duplicates dropped=%s",
        year,
        filings,
        empty,
        len(docs),
        duplicates,
    )
    return docs


@task
def write_parquet(docs: list[Document], year: int) -> Path:
    path = year_path(settings.corpus_dir, year)
    n = write_year(docs, path)
    get_run_logger().info("wrote %s rows to %s (%.1f MB)", n, path, path.stat().st_size / 1e6)
    return path


@task
def load_postgres(docs: list[Document]) -> int:
    engine = db.make_engine(settings.database_url)
    db.ensure_schema(engine)
    inserted = db.upsert_documents(engine, docs)
    get_run_logger().info(
        "postgres: %s new rows, %s already present", inserted, len(docs) - inserted
    )
    return inserted


@task
def publish_kafka(docs: list[Document]) -> int:
    kafka.ensure_topic(settings.kafka_bootstrap)
    n = kafka.publish_doc_ids(
        settings.kafka_bootstrap,
        ((d["doc_id"], {"doc_id": d["doc_id"], "year": d["year"]}) for d in docs),
    )
    get_run_logger().info("kafka: %s messages acknowledged on %s", n, kafka.TOPIC)
    return n


@flow(name="collect")
def collect(year: int, limit: int | None = None) -> dict[str, int]:
    """Collect one filing year end to end. Safe to re-run: Parquet is rewritten,
    Postgres skips known ids, Kafka messages are keyed by doc_id."""
    names = load_company_names(settings.sec_user_agent, settings.data_dir / "sec" / "tickers.json")
    docs = download_and_split(year, limit, names)
    write_parquet(docs, year)
    inserted = load_postgres(docs)
    published = publish_kafka(docs)
    return {"paragraphs": len(docs), "inserted": inserted, "published": published}
