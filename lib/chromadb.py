import chromadb
from chromadb.config import Settings

from lib.settings import settings

chroma_client = chromadb.HttpClient(
    host=settings.chromadb.host,
    port=settings.chromadb.port,
    settings=Settings(anonymized_telemetry=False),
)
