import pycouchdb
import pycouchdb.exceptions

from lib.settings import settings


def create_indizes_if_not_exist(db: pycouchdb.client.Database):
    indizes = [
        {
            "index": {"fields": ["timestamp"]},
            "name": "idx_timestamp",
            "type": "json",
        },
        {
            "index": {"fields": ["type"]},
            "name": "idx_type",
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


# @st.cache_data()
def couchdb() -> pycouchdb.client.Database:
    server = pycouchdb.Server(settings.couchdb.url)

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

    return db
