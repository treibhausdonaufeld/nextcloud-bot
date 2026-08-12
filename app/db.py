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
    lemmas,
    tokenize="unicode61 remove_diacritics 2"
)
"""


async def _table_columns(table: str) -> set[str]:
    rows = await database.fetch_all(f"PRAGMA table_info({table})")
    return {row._mapping["name"] for row in rows}


async def _migrate_schema() -> None:
    """Bring pre-existing databases up to the current schema.

    Edgy's create_all does not alter existing tables, and the FTS5 virtual
    table cannot be altered at all — when its layout changed, drop and
    rebuild it from the stored pages and decisions.
    """
    from app.textnorm import index_terms

    if "context" not in await _table_columns("decisions"):
        await database.execute(
            "ALTER TABLE decisions ADD COLUMN context TEXT NOT NULL DEFAULT ''"
        )
        logger.info("Migrated decisions table: added context column")

    protocol_columns = await _table_columns("protocols")
    if "time" not in protocol_columns:
        await database.execute(
            "ALTER TABLE protocols ADD COLUMN time VARCHAR(32) NOT NULL DEFAULT ''"
        )
        logger.info("Migrated protocols table: added time column")
    if "location_type" not in protocol_columns:
        await database.execute(
            "ALTER TABLE protocols ADD COLUMN location_type VARCHAR(32)"
            " NOT NULL DEFAULT ''"
        )
        logger.info("Migrated protocols table: added location_type column")
    if "preview" not in protocol_columns:
        await database.execute(
            "ALTER TABLE protocols ADD COLUMN preview VARCHAR(512) NOT NULL DEFAULT ''"
        )
        logger.info("Migrated protocols table: added preview column")

    if "chat_channels" not in await _table_columns("groups"):
        await database.execute(
            "ALTER TABLE groups ADD COLUMN chat_channels JSON NOT NULL DEFAULT '[]'"
        )
        logger.info("Migrated groups table: added chat_channels column")

    user_columns = await _table_columns("users")
    if "authentik_username" not in user_columns:
        await database.execute(
            "ALTER TABLE users ADD COLUMN authentik_username VARCHAR(255)"
            " NOT NULL DEFAULT ''"
        )
        logger.info("Migrated users table: added authentik_username column")
    if "authentik_groups" not in user_columns:
        await database.execute(
            "ALTER TABLE users ADD COLUMN authentik_groups JSON NOT NULL DEFAULT '[]'"
        )
        logger.info("Migrated users table: added authentik_groups column")

    # Early protocol_media versions stored attachment bytes as a database
    # blob; media now lives on disk. Drop the old table (attachments are
    # re-fetched from Nextcloud on the next sync) and recreate it with the
    # current schema.
    protocol_media_columns = await _table_columns("protocol_media")
    if "data" in protocol_media_columns:
        await database.execute("DROP TABLE protocol_media")
        await registry.create_all()
        logger.info("Migrated protocol_media table: attachments moved to disk")

    if "lemmas" in await _table_columns("search_index"):
        return

    logger.info("Rebuilding search index with lemmas column")
    await database.execute("DROP TABLE IF EXISTS search_index")
    await database.execute(SEARCH_INDEX_DDL)

    pages = await database.fetch_all(
        "SELECT page_id, title, content FROM collective_pages"
        " WHERE content IS NOT NULL AND TRIM(content) != ''"
    )
    for row in pages:
        page = row._mapping
        await database.execute(
            "INSERT INTO search_index (doc_type, doc_id, title, body, lemmas)"
            " VALUES ('page', :i, :title, :body, :lemmas)",
            {
                "i": str(page["page_id"]),
                "title": page["title"],
                "body": page["content"],
                "lemmas": index_terms(page["title"] + " " + page["content"]),
            },
        )

    decisions = await database.fetch_all(
        "SELECT id, title, text, objections, context, group_name, date FROM decisions"
    )
    for row in decisions:
        decision = row._mapping
        body = " ".join(
            part
            for part in (
                decision["title"],
                decision["text"],
                decision["objections"],
                decision["context"],
                decision["group_name"],
                decision["date"],
            )
            if part
        )
        await database.execute(
            "INSERT INTO search_index (doc_type, doc_id, title, body, lemmas)"
            " VALUES ('decision', :i, :title, :body, :lemmas)",
            {
                "i": str(decision["id"]),
                "title": decision["title"],
                "body": body,
                "lemmas": index_terms(body),
            },
        )
    logger.info("Reindexed %d pages and %d decisions", len(pages), len(decisions))


async def _init_schema() -> None:
    await database.connect()
    await database.execute("PRAGMA journal_mode=WAL")
    await registry.create_all()
    await database.execute(SEARCH_INDEX_DDL)
    await _migrate_schema()


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
    """Insert or replace a document in the FTS5 index.

    Besides the raw title/body, a `lemmas` column with lemmatized tokens and
    compound-word parts is indexed so inflected German queries still match.
    """
    from app.textnorm import index_terms

    lemmas = index_terms(title + " " + body)

    async def _update() -> None:
        await database.execute(
            "DELETE FROM search_index WHERE doc_type = :t AND doc_id = :i",
            {"t": doc_type, "i": doc_id},
        )
        await database.execute(
            "INSERT INTO search_index (doc_type, doc_id, title, body, lemmas)"
            " VALUES (:t, :i, :title, :body, :lemmas)",
            {
                "t": doc_type,
                "i": doc_id,
                "title": title,
                "body": body,
                "lemmas": lemmas,
            },
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
    syntax) and suffixed with * for prefix matching. Each term is expanded
    with its lemma and compound parts (OR-ed), so "Gießkannen" also matches
    documents that only contain "Gießkanne" — and vice versa via the
    document-side `lemmas` column.
    """
    from app.textnorm import token_variants

    groups = []
    for term in query.split():
        term = term.replace('"', "")
        if not term:
            continue
        quoted = [f'"{variant}"*' for variant in token_variants(term)]
        groups.append(quoted[0] if len(quoted) == 1 else f"({' OR '.join(quoted)})")
    return " ".join(groups)


def search(
    query: str, doc_types: list[str] | None = None, limit: int = 25
) -> list[dict]:
    """Full-text search; returns dicts with doc_type, doc_id, title, snippet."""
    match = fts_escape(query)
    if not match:
        return []

    # column weights: title counts double, lemma matches score lower than
    # literal body matches
    sql = (
        "SELECT doc_type, doc_id, title,"
        " snippet(search_index, 3, '<mark>', '</mark>', ' … ', 32) AS snippet,"
        " bm25(search_index, 0.0, 0.0, 2.0, 1.0, 0.5) AS score"
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
