from functools import lru_cache

import pycouchdb
import pycouchdb.exceptions

from lib.settings import settings


def create_user_index(db: pycouchdb.client.Database):
    MARKDOWN_FIELD_NAME = "content"

    # --- The JavaScript Map Function ---
    # This code runs *inside* CouchDB (it's JavaScript, not Python)
    map_function = rf"""
    function(doc) {{
        if (doc.{MARKDOWN_FIELD_NAME}) {{

            // Regex to find all mentions in the format mention://user/username
            // The 'g' flag ensures it finds *all* matches, not just the first one.
            // Allow letters, numbers, underscores, hyphens and dots
            const regex = /mention:\/\/user\/([A-Za-z0-9_.-]+)/g;

            // Use matchAll to get an iterator of all matches
            const matches = doc.{MARKDOWN_FIELD_NAME}.matchAll(regex);

            for (const match of matches) {{
                // match[1] is the captured group (e.g., "fabian_helm")
                // We emit the username as the key and the number 1 as the value.
                emit(match[1], 1);
            }}
        }}
    }}
    """

    # The built-in _sum reducer will sum all the '1's for each key
    reduce_function = "_sum"

    # Define the design document structure
    DESIGN_DOC_ID = "_design/mentions"
    design_doc = {
        "_id": DESIGN_DOC_ID,  # The ID must start with _design/
        "language": "javascript",
        "views": {"by_user": {"map": map_function, "reduce": reduce_function}},
    }

    # if DESIGN_DOC_ID in db:
    #     db.delete(design_doc)

    if DESIGN_DOC_ID not in db:
        db.save(design_doc)


def create_indizes_if_not_exist(db: pycouchdb.client.Database):
    indizes = [
        {
            "index": {"fields": ["updated_at"]},
            "name": "idx_updated_at",
            "type": "json",
        },
        {
            "index": {"fields": ["ocs.timestamp"]},
            "name": "idx_timestamp",
            "type": "json",
        },
        {
            "index": {"fields": ["date"]},
            "name": "idx_date",
            "type": "json",
        },
        {
            "index": {"fields": ["type"]},
            "name": "idx_type",
            "type": "json",
        },
        {
            "index": {"fields": ["subtype"]},
            "name": "idx_subtype",
            "type": "json",
        },
        # Composite index required when using a selector on `type` and
        # sorting by `updated_at`. Mango requires an index whose fields
        # start with the equality fields used in the selector followed by
        # the sort fields in the requested order.
        {
            "index": {"fields": ["type", "updated_at"]},
            "name": "idx_type_updated_at",
            "type": "json",
        },
    ]

    # CouchDB will return 200 if index exists or create it otherwise.
    try:
        for index_def in indizes:
            response, _result = db.resource.post("_index", json=index_def)
            response.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Failed to create index: {e}") from e


@lru_cache(maxsize=1)
def couchdb() -> pycouchdb.client.Database:
    """Return a cached pycouchdb Database instance for the configured DB.

    The result is cached in-process so repeated calls reuse the same
    connection/object. To force a new connection, restart the process or
    clear the cache with `lib.couchdb.couchdb.cache_clear()`.
    """
    server = pycouchdb.Server(str(settings.couchdb.url))

    try:
        db = server.database(settings.couchdb.database_name)
    except pycouchdb.exceptions.NotFound:
        db = server.create(settings.couchdb.database_name)

    if db is None:
        raise RuntimeError(
            f"Could not access or create CouchDB database "
            f"'{settings.couchdb.database_name}' at {settings.couchdb.url}"
        )

    create_indizes_if_not_exist(db)
    create_user_index(db)

    return db
