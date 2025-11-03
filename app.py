import logging
from typing import Generator, List

import streamlit as st
from google import genai
from langchain.retrievers import EnsembleRetriever
from langchain_chroma import Chroma
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

from lib.chromadb import chroma_client, langchain_embeddings
from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.collective_page import (
    CollectivePage,
)
from lib.settings import _, settings

logger = logging.getLogger(__name__)


def prompt_ai_stream(
    pages: List[CollectivePage], question: str
) -> Generator[str, None, None]:
    """Ask AI a question and stream the response token by token."""
    context = "\n\n".join(
        [
            f"Titel: {p.title}\nDatum: {p.formatted_timestamp}\nInhalt: {p.content}"
            for p in pages
            if p.content
        ]
    )

    prompt = f"""Du bist ein hilfreicher Assistent, der Informationen aus
    Kollektiv-Seiten zusammenfasst und Fragen dazu beantwortet.
    Nutze den folgenden Kontext, um die Frage zu beantworten. Wenn die
    Information nicht im Kontext vorhanden ist, antworte mit
    "Die Information ist nicht verfügbar".
    Antworte immer in der Sprache in der die Frage gestellt wurde.

    Kontext:
    {context}

    Frage: {question}
    Antwort:"""

    client = genai.Client(api_key=settings.gemini_api_key)

    # Stream the response
    for chunk in client.models.generate_content_stream(
        model=settings.gemini_model,
        contents=prompt,
    ):
        if chunk.text:
            yield chunk.text


def prompt_ai(pages: List[CollectivePage], question: str) -> str | None:
    """Ask AI a question based on collective pages context (non-streaming fallback)."""
    context = "\n\n".join(
        [
            f"Titel: {p.title}\nDatum: {p.formatted_timestamp}\nInhalt: {p.content}"
            for p in pages
            if p.content
        ]
    )

    prompt = f"""Du bist ein hilfreicher Assistent, der Informationen aus Kollektiv-Seiten
    zusammenfasst und Fragen dazu beantwortet.
    Nutze den folgenden Kontext, um die Frage zu beantworten.
    Wenn die Information nicht im Kontext vorhanden ist,
    antworte mit "Die Information ist nicht verfügbar".
    Antworte immer in der Sprache in der die Frage gestellt wurde.

    Kontext:
    {context}

    Frage: {question}
    Antwort:"""

    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
    )

    return response.text


@st.cache_resource(ttl="1d")
def get_hybrid_retriever():
    """Create a hybrid retriever combining semantic search (Chroma) and keyword search (BM25)."""
    # Semantic search with Chroma
    vectorstore = Chroma(
        client=chroma_client,
        collection_name="collective_page",
        embedding_function=langchain_embeddings,
    )
    semantic_retriever = vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": 20},  # Retrieve up to 20 chunks
    )

    # Load documents directly from ChromaDB for BM25 (more efficient)
    try:
        collection = chroma_client.get_collection("collective_page")

        # Get all documents from ChromaDB (only fetches IDs, documents, and metadata)
        result = collection.get(include=["documents", "metadatas"])

        # Create LangChain documents from ChromaDB results
        documents = []
        if result["documents"] and result["metadatas"]:
            print("Amount of documents loaded: ", len(result["documents"]))
            for doc, metadata in zip(result["documents"], result["metadatas"]):
                if doc:  # Only include non-empty documents
                    documents.append(
                        Document(page_content=doc, metadata=metadata or {})
                    )

        logger.info(
            f"Loaded {len(documents)} documents from ChromaDB for BM25 indexing"
        )

        # BM25 keyword search
        bm25_retriever = BM25Retriever.from_documents(documents)
        bm25_retriever.k = 20

        # Combine both retrievers (70% semantic, 30% keyword)
        ensemble_retriever = EnsembleRetriever(
            retrievers=[semantic_retriever, bm25_retriever], weights=[0.7, 0.3]
        )

        return ensemble_retriever
    except Exception as e:
        logger.warning(
            f"Could not create BM25 retriever, falling back to semantic only: {e}"
        )
        return semantic_retriever


# Streamlit app starts here
title = _("{common_name} Dashboard").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="🏡", layout="wide")

menu()

db = couchdb()


st.title(title)

# AI Question Section
st.subheader("🤖 " + _("Ask a Question") + " 🤖")

retriever = get_hybrid_retriever()

col1, col2, col3 = st.columns([3, 1, 1])
question = col1.text_input(
    _("Search or ask about collective pages"),
    placeholder=_("e.g., What decisions were made about...?"),
    label_visibility="collapsed",
)
num_results = col2.slider(
    _("Context size"),
    min_value=1,
    max_value=20,
    value=5,
    help=_("Number of documents to include in context"),
)
use_streaming = col3.checkbox(
    _("Stream response"),
    value=True,
    help=_("Show answer as it's being generated"),
)

if question:
    # Use LangChain hybrid retriever to get relevant chunks
    with st.spinner(_("Searching documents...")):
        # Limit results (EnsembleRetriever doesn't have search_kwargs, so we slice)
        retrieved_docs = retriever.invoke(question)[:num_results]

    if retrieved_docs:
        # Get unique page IDs from metadata
        unique_page_ids = list(
            set(
                doc.metadata.get("page_id")
                for doc in retrieved_docs
                if doc.metadata.get("page_id") is not None
            )
        )

        # Load the actual pages
        matching_pages = []
        for page_id in unique_page_ids:
            try:
                if isinstance(page_id, int):
                    page = CollectivePage.get_from_page_id(page_id)
                    matching_pages.append(page)
            except Exception as e:
                logger.warning(f"Could not load page {page_id}: {e}")

        if matching_pages:
            st.markdown(f"### {_('Answer')}:")

            # Stream or display full answer
            if use_streaming:
                answer_placeholder = st.empty()
                full_answer = ""

                for chunk in prompt_ai_stream(matching_pages, question):
                    full_answer += chunk
                    answer_placeholder.markdown(full_answer + "▌")

                # Remove cursor after streaming is complete
                answer_placeholder.markdown(full_answer)
            else:
                with st.spinner(_("Thinking...")):
                    answer = prompt_ai(matching_pages, question)
                st.markdown(answer)

            st.markdown(f"### {_('Source Documents')}:")

            # Create dataframe with source documents
            source_data = []
            for p in matching_pages:
                source_data.append(
                    {
                        _("Title"): p.title,
                        _("Date"): p.formatted_timestamp,
                        _("ID"): p.id,
                        _("File Path"): p.ocs.filePath if p.ocs else "",
                        _("URL"): p.url or "",
                    }
                )

            st.dataframe(
                source_data,
                column_config={
                    _("URL"): st.column_config.LinkColumn(
                        _("Link"), display_text=_("Open page")
                    ),
                },
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.warning(_("No matching pages found."))
    else:
        st.warning(_("No matching pages found."))
