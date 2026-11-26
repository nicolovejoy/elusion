import os
import socket

import pytest

# MLflow 3.17 refuses the file:// store unless opted in; the tracking tests use one under
# tmp_path so nothing leaves the machine. The hint is a one-line stderr advert for an agent skill.
os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def requires_service(host: str, port: int, name: str):
    return pytest.mark.skipif(not _reachable(host, port), reason=f"{name} not reachable")


@pytest.fixture(scope="session")
def board_engine():
    """An engine on a throwaway database with the `concept_log` table in it. The log
    refuses UPDATE and DELETE, so tests cannot tidy up after themselves; a database made
    for the run and dropped after it keeps them off the real one. Each test uses its own
    concept slug, so tests do not see each other's rows."""
    import uuid

    import sqlalchemy as sa

    from elusion.board import log
    from elusion.collect.db import make_engine
    from elusion.config import settings

    if not _reachable("localhost", 5433):
        pytest.skip("postgres not reachable")
    name = f"elusion_test_{uuid.uuid4().hex[:8]}"
    admin = make_engine(settings.database_url).execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f"CREATE DATABASE {name}"))
    engine = make_engine(
        sa.make_url(settings.database_url).set(database=name).render_as_string(False)
    )
    try:
        log.ensure_schema(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


@pytest.fixture
def index_engine():
    """An engine on a throwaway database holding the index tables and the review tables.

    One database per test, because the index flow tests need different pairs and a fresh
    database costs a fraction of a second. `CREATE EXTENSION vector` is run by the code
    under test (`ask.index.ensure_schema`), not here, so this fixture proves the code makes
    the extension itself. A server without pgvector fails the test with Postgres's own
    message; it is not skipped, because a silent skip would hide a wrong image."""
    import uuid

    import sqlalchemy as sa

    from elusion.ask import index
    from elusion.collect.db import make_engine
    from elusion.config import settings
    from elusion.review import db as review_db

    if not _reachable("localhost", 5433):
        pytest.skip("postgres not reachable")
    name = f"elusion_test_{uuid.uuid4().hex[:8]}"
    admin = make_engine(settings.database_url).execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f"CREATE DATABASE {name}"))
    engine = make_engine(
        sa.make_url(settings.database_url).set(database=name).render_as_string(False)
    )
    try:
        index.ensure_schema(engine)
        review_db.ensure_schema(engine)
        # Task C adds the ask tables' own ensure_schema here.
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


@pytest.fixture(autouse=True)
def made_up_criteria(monkeypatch):
    """The flow tests review made-up criteria ("c", "s", "sys"); file them under the 10-K
    corpus, as a real criterion is filed in `elusion.corpora`. `monkeypatch` undoes it."""
    from elusion import corpora

    for name in ("c", "s", "sys"):
        monkeypatch.setitem(corpora.CRITERION_CORPUS, name, corpora.EDGAR)
