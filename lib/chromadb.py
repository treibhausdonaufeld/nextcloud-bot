import chromadb
import httpx
from chromadb.config import Settings
from chromadb.utils import embedding_functions

from lib.settings import settings

# Single unified collection name for all embeddings
UNIFIED_COLLECTION_NAME = "collection"


class CustomHuggingFaceEmbeddingServer(embedding_functions.HuggingFaceEmbeddingServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._session = httpx.Client(timeout=30.0)


chroma_client = chromadb.HttpClient(
    host=settings.chromadb.host,
    port=settings.chromadb.port,
    settings=Settings(
        anonymized_telemetry=False, chroma_query_request_timeout_seconds=600
    ),
)
embedding_function: (
    embedding_functions.GoogleGenerativeAiEmbeddingFunction
    | CustomHuggingFaceEmbeddingServer
    | None
) = None

# Use ChromaDB native embedding functions
if settings.chromadb.gemini_api_key:
    embedding_function = embedding_functions.GoogleGenerativeAiEmbeddingFunction(
        api_key=settings.chromadb.gemini_api_key,
        task_type="retrieval_document",
        model_name="gemini-embedding-001",
        api_key_env_var="CHROMADB__GEMINI_API_KEY",
    )
elif settings.chromadb.hf_embedding_server_url:
    embedding_function = CustomHuggingFaceEmbeddingServer(
        url=settings.chromadb.hf_embedding_server_url,
    )
else:
    embedding_function = None


def get_unified_collection():
    """Get or create the unified collection for all embeddings."""
    return chroma_client.get_or_create_collection(
        UNIFIED_COLLECTION_NAME,
        embedding_function=embedding_function,  # type: ignore
    )
