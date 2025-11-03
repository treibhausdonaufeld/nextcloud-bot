from typing import List

import chromadb
from chromadb.api.types import EmbeddingFunction
from chromadb.config import Settings
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_huggingface import HuggingFaceEmbeddings
from pydantic import SecretStr

from lib.settings import settings


class LangChainEmbeddingAdapter(EmbeddingFunction):
    """Adapter to make LangChain embeddings compatible with ChromaDB."""

    def __init__(self, langchain_embeddings, function_name: str):
        self._embeddings = langchain_embeddings
        self._name = function_name

    def __call__(self, input: List[str]) -> List[List[float]]:
        """Embed a list of texts."""
        embeddings = self._embeddings.embed_documents(input)
        # Ensure we return List[List[float]] not List[ndarray]
        return [[float(x) for x in emb] for emb in embeddings]

    def name(self) -> str:
        """Return the name of the embedding function."""
        return self._name


chroma_client = chromadb.HttpClient(
    host=settings.chromadb.host,
    port=settings.chromadb.port,
    settings=Settings(anonymized_telemetry=False),
)
embedding_function: LangChainEmbeddingAdapter | None = None
langchain_embeddings: GoogleGenerativeAIEmbeddings | HuggingFaceEmbeddings | None = None

# Use LangChain embeddings for better performance and caching
if settings.chromadb.gemini_api_key:
    langchain_embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001",
        google_api_key=SecretStr(settings.chromadb.gemini_api_key),
        task_type="retrieval_document",
    )
    embedding_function = LangChainEmbeddingAdapter(
        langchain_embeddings, "google-generative-ai-embeddings"
    )
elif settings.chromadb.hf_embedding_server_url:
    # Use a high-quality sentence transformer model
    langchain_embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-mpnet-base-v2",
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    embedding_function = LangChainEmbeddingAdapter(
        langchain_embeddings, "huggingface-embeddings"
    )
