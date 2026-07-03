from collections import Counter

import edgy

from app.db import fetch_all_sql, run_db
from app.models.base import BaseDBModel
from app.settings import user_regex


class Mention(BaseDBModel):
    """Occurrences of `mention://user/<name>` per page.

    Replaces the CouchDB `_design/mentions` map/reduce view: rows are
    rebuilt whenever a page is saved, so aggregations are plain SQL.
    """

    page_id: int = edgy.BigIntegerField(index=True)
    username: str = edgy.CharField(max_length=255, index=True)
    mention_count: int = edgy.IntegerField(default=1)

    class Meta:
        tablename = "mentions"
        unique_together = [("page_id", "username")]

    @classmethod
    def rebuild_for_page(cls, page_id: int, content: str) -> None:
        """Replace all mention rows of a page from its markdown content."""
        counts = Counter(user_regex.findall(content or ""))

        async def _rebuild() -> None:
            await cls.query.filter(page_id=page_id).delete()
            for username, count in counts.items():
                await cls.query.create(
                    page_id=page_id, username=username, mention_count=count
                )

        run_db(_rebuild())

    @classmethod
    def counts_by_user(cls) -> dict[str, int]:
        """Total mention count per username (descending)."""
        rows = fetch_all_sql(
            "SELECT username, SUM(mention_count) AS total FROM mentions"
            " GROUP BY username ORDER BY total DESC"
        )
        return {row["username"]: row["total"] for row in rows}

    @classmethod
    def pages_for_user(cls, username: str) -> list[dict]:
        """Pages mentioning a user, with title/count, newest first."""
        return fetch_all_sql(
            "SELECT m.page_id, m.mention_count, p.title, p.timestamp, p.slug,"
            " p.collective_path"
            " FROM mentions m"
            " JOIN collective_pages p ON p.page_id = m.page_id"
            " WHERE m.username = :username"
            " ORDER BY p.timestamp DESC",
            {"username": username},
        )

    @classmethod
    def all_user_page_relations(cls) -> list[dict]:
        """All (username, page) mention pairs for the network graph."""
        return fetch_all_sql(
            "SELECT m.username, m.page_id, m.mention_count, p.title"
            " FROM mentions m"
            " JOIN collective_pages p ON p.page_id = m.page_id"
            " ORDER BY m.mention_count DESC"
        )
