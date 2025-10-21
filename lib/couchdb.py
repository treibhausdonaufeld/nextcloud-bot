import pycouchdb
import pycouchdb.exceptions

from lib.settings import settings


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

    return db
