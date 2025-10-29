import chromadb

from lib.settings import settings

chroma_client = chromadb.HttpClient(
    host=settings.chromadb.host, port=settings.chromadb.port
)
