import chromadb
import google.generativeai.client as genai
from chromadb.config import Settings
from chromadb.utils import embedding_functions

from lib.settings import settings

chroma_client = chromadb.HttpClient(
    host=settings.chromadb.host,
    port=settings.chromadb.port,
    settings=Settings(anonymized_telemetry=False),
)

if settings.chromadb.gemini_api_key:
    genai.configure(api_key=settings.chromadb.gemini_api_key)

    embedding_function = embedding_functions.GoogleGenerativeAiEmbeddingFunction(
        api_key=settings.chromadb.gemini_api_key,
        task_type="retrieval_document",
        model_name="gemini-embedding-001",
        api_key_env_var="CHROMADB__GEMINI_API_KEY",
    )
elif settings.chromadb.hf_embedding_server_url:
    embedding_function = embedding_functions.HuggingFaceEmbeddingServer(
        url=settings.chromadb.hf_embedding_server_url,
    )
else:
    embedding_function = None
