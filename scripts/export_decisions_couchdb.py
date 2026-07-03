#!/usr/bin/env python3
"""Export all logbook decisions from the old CouchDB to a JSON file.

Run this against the OLD deployment (before the SQLite migration), then
import the file into the new app with:

    python cli.py import-decisions decisions_export.json

The script only uses the Python standard library, so it can be copied into
the old container and run there:

    python3 export_decisions_couchdb.py \
        --url http://admin:password@localhost:5984/ \
        --database nextcloud_bot \
        --base-url https://cloud.example.org \
        --output decisions_export.json

When --base-url is given, decisions whose protocol page may no longer exist
after the migration keep a working link: the page URL is resolved from the
old CollectivePage documents and stored as `external_link` (unless the
decision already has one).

Duplicate safety: the export keeps each decision's `page_id` and title, which
is exactly what the new app's Decision natural key is built from. Importing
is therefore an upsert, and a later `cli.py sync --update-all` re-extracts
page-bound decisions (deleting the page's old rows first), so no duplicates
are created either way.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.parse
import urllib.request

BATCH_SIZE = 200


def make_request(base_url: str, auth_header: str | None, path: str, payload: dict):
    url = base_url.rstrip("/") + path
    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    if auth_header:
        request.add_header("Authorization", auth_header)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def split_credentials(url: str) -> tuple[str, str | None]:
    """Extract basic-auth credentials from the URL (pycouchdb style)."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.username is None:
        return url, None
    credentials = f"{parsed.username}:{parsed.password or ''}"
    auth_header = "Basic " + base64.b64encode(credentials.encode()).decode()
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc += f":{parsed.port}"
    clean = urllib.parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
    )
    return clean, auth_header


def find_all(
    base_url: str, auth: str | None, database: str, selector: dict
) -> list[dict]:
    """Fetch all docs matching the Mango selector, following bookmarks."""
    docs: list[dict] = []
    bookmark = None
    while True:
        payload: dict = {"selector": selector, "limit": BATCH_SIZE}
        if bookmark:
            payload["bookmark"] = bookmark
        result = make_request(base_url, auth, f"/{database}/_find", payload)
        batch = result.get("docs", [])
        if not batch:
            return docs
        docs.extend(batch)
        bookmark = result.get("bookmark")


def build_page_urls(
    pages: list[dict], nextcloud_base_url: str, collectives_id: int
) -> dict[int, str]:
    """Rebuild the public page URL per page_id (like CollectivePage.url did)."""
    urls: dict[int, str] = {}
    for doc in pages:
        ocs = doc.get("ocs") or {}
        page_id = ocs.get("id")
        slug = ocs.get("slug")
        collective_path = ocs.get("collectivePath") or ""
        if not page_id or not slug or "/" not in collective_path:
            continue
        collective_name = collective_path.split("/")[1]
        urls[page_id] = (
            nextcloud_base_url.rstrip("/")
            + f"/apps/collectives/{collective_name}-{collectives_id}"
            + f"/{slug}-{page_id}"
        )
    return urls


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("COUCHDB__URL", "http://admin:password@localhost:5984/"),
        help="CouchDB URL incl. credentials (default: $COUCHDB__URL)",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("COUCHDB__DATABASE_NAME", "nextcloud_bot"),
        help="CouchDB database name (default: $COUCHDB__DATABASE_NAME or nextcloud_bot)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("NEXTCLOUD__BASE_URL", ""),
        help="Nextcloud base URL, used to fill external_link from the old page URLs",
    )
    parser.add_argument(
        "--collectives-id",
        type=int,
        default=int(os.environ.get("NEXTCLOUD__COLLECTIVES_ID", "1")),
        help="Collectives id used in page URLs (default: 1)",
    )
    parser.add_argument("--output", default="decisions_export.json")
    args = parser.parse_args()

    base_url, auth = split_credentials(args.url)

    decisions = find_all(base_url, auth, args.database, {"type": "Decision"})
    print(f"Fetched {len(decisions)} decisions from CouchDB", file=sys.stderr)

    page_urls: dict[int, str] = {}
    if args.base_url:
        pages = find_all(base_url, auth, args.database, {"type": "CollectivePage"})
        page_urls = build_page_urls(pages, args.base_url, args.collectives_id)
        print(f"Resolved URLs for {len(page_urls)} pages", file=sys.stderr)

    exported = []
    for doc in decisions:
        page_id = doc.get("page_id")
        exported.append(
            {
                "title": doc.get("title") or "",
                "text": doc.get("text") or "",
                "date": doc.get("date") or "",
                "page_id": page_id,
                "group_name": doc.get("group_name") or "",
                "valid_until": doc.get("valid_until") or "",
                "objections": doc.get("objections") or "",
                "external_link": doc.get("external_link")
                or (page_urls.get(page_id, "") if page_id else ""),
            }
        )

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(exported, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(exported)} decisions to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
