"""Database layer: Edgy registry on SQLite plus a sync bridge.

Edgy is async-first, but most of this codebase (markdown parsing, the
background worker, mail handling) is synchronous. All database access is
therefore funneled through a single dedicated event-loop thread via
`run_db()`: web handlers (which Ravyn runs in a threadpool when declared
sync), the worker thread and the CLI all share one loop and one connection
pool, which keeps databasez/aiosqlite happy and serializes SQLite writes.
"""

import asyncio
import atexit
import logging
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

import edgy

from app.settings import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

database = edgy.Database(settings.database_url)
registry = edgy.Registry(database=database)


_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Start (once) and return the dedicated database event loop."""
    global _loop
    with _loop_lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name="db-loop", daemon=True
            )
            thread.start()
            _loop = loop
            atexit.register(_shutdown_loop)
    return _loop


def _shutdown_loop() -> None:
    global _loop
    if _loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(database.disconnect(), _loop).result(timeout=5)
    except Exception:  # pragma: no cover - best effort on interpreter exit
        pass
    _loop.call_soon_threadsafe(_loop.stop)
    _loop = None


def run_db(coro: Coroutine[Any, Any, T]) -> T:
    """Run a database coroutine on the dedicated loop and return its result."""
    loop = _ensure_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


SEARCH_INDEX_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    doc_type UNINDEXED,
    doc_id UNINDEXED,
    title,
    body,
    tokenize="unicode61 remove_diacritics 2"
)
"""


async def _init_schema() -> None:
    await database.connect()
    await database.execute("PRAGMA journal_mode=WAL")
    await registry.create_all()
    await database.execute(SEARCH_INDEX_DDL)


def init_db() -> None:
    """Create the SQLite file, tables, indexes and the FTS5 search table."""
    # Models register themselves on import; make sure they all exist in the
    # registry before create_all runs (entrypoints may import models lazily).
    import app.models  # noqa: F401

    db_path = _sqlite_path(settings.database_url)
    if db_path:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    run_db(_init_schema())
    logger.info("Database initialized at %s", db_path or settings.database_url)


def _sqlite_path(url: str) -> Path | None:
    if ":memory:" in url:
        return None
    marker = ":///"
    if marker not in url:
        return None
    return Path(url.split(marker, 1)[1])


# --- Full-text search -------------------------------------------------------


def update_search_index(doc_type: str, doc_id: str, title: str, body: str) -> None:
    """Insert or replace a document in the FTS5 index."""

    async def _update() -> None:
        await database.execute(
            "DELETE FROM search_index WHERE doc_type = :t AND doc_id = :i",
            {"t": doc_type, "i": doc_id},
        )
        await database.execute(
            "INSERT INTO search_index (doc_type, doc_id, title, body)"
            " VALUES (:t, :i, :title, :body)",
            {"t": doc_type, "i": doc_id, "title": title, "body": body},
        )

    run_db(_update())


def remove_from_search_index(doc_type: str, doc_id: str) -> None:
    async def _remove() -> None:
        await database.execute(
            "DELETE FROM search_index WHERE doc_type = :t AND doc_id = :i",
            {"t": doc_type, "i": doc_id},
        )

    run_db(_remove())


def fts_escape(query: str) -> str:
    """Turn free-form user input into a safe FTS5 MATCH expression.

    Every whitespace-separated term is quoted (disabling FTS5 operator
    syntax) and suffixed with * for prefix matching.
    """
    terms = [t.replace('"', "") for t in query.split()]
    return " ".join(f'"{t}"*' for t in terms if t)


def search(
    query: str, doc_types: list[str] | None = None, limit: int = 25
) -> list[dict]:
    """Full-text search; returns dicts with doc_type, doc_id, title, snippet."""
    match = fts_escape(query)
    if not match:
        return []

    sql = (
        "SELECT doc_type, doc_id, title,"
        " snippet(search_index, 3, '<mark>', '</mark>', ' … ', 32) AS snippet,"
        " bm25(search_index) AS score"
        " FROM search_index WHERE search_index MATCH :q"
    )
    values: dict[str, Any] = {"q": match}
    if doc_types:
        placeholders = ", ".join(f":dt{i}" for i in range(len(doc_types)))
        sql += f" AND doc_type IN ({placeholders})"
        values |= {f"dt{i}": dt for i, dt in enumerate(doc_types)}
    sql += " ORDER BY score LIMIT :limit"
    values["limit"] = limit

    async def _search() -> list[dict]:
        rows = await database.fetch_all(sql, values)
        return [dict(row._mapping) for row in rows]

    return run_db(_search())


def fetch_all_sql(sql: str, values: dict | None = None) -> list[dict]:
    """Run a raw read-only SQL query and return rows as dicts."""

    async def _fetch() -> list[dict]:
        rows = await database.fetch_all(sql, values)
        return [dict(row._mapping) for row in rows]

    return run_db(_fetch())
