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
from lib.nextcloud.models.collective_page import CollectivePage
from lib.settings import _, settings

logger = logging.getLogger(__name__)


def prompt_ai_stream(docs: List[Document], question: str) -> Generator[str, None, None]:
    """Ask AI a question and stream the response token by token."""
    context = "\n\n".join(
        [
            f"Titel: {doc.metadata.get('title', 'N/A')}\n"
            f"Datum: {doc.metadata.get('timestamp', 'N/A')}\n"
            f"Inhalt: {doc.page_content}"
            for doc in docs
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
    _("Chunks size"),
    min_value=1,
    max_value=50,
    value=25,
    help=_("Number of chunks to include in context"),
)
load_full_pages = col3.checkbox(
    _("Full pages"),
    value=False,
    help=_("Load full pages instead of excerpts"),
)

if question:
    # Use LangChain hybrid retriever to get relevant chunks
    with st.spinner(_("Searching documents...")):
        # Limit results (EnsembleRetriever doesn't have search_kwargs, so we slice)
        retrieved_docs = retriever.invoke(question)[:num_results]

    if retrieved_docs:
        st.markdown(f"### {_('Answer')}")

        if load_full_pages:
            # Load full pages for each retrieved chunk
            full_pages = {}
            for doc in retrieved_docs:
                page_id = doc.metadata.get("page_id")
                if page_id in full_pages:
                    continue  # Already loaded

                if page_id:
                    try:
                        page = CollectivePage.get_from_page_id(page_id)
                        full_content = (
                            f"Titel: {page.title}\n"
                            f"Datum: {page.formatted_timestamp or ''}\n"
                            f"Inhalt: {page.content if page.content else ''}"
                        )
                        full_pages[page_id] = Document(
                            page_content=full_content,
                            metadata=doc.metadata,
                        )
                    except Exception as e:
                        logger.warning(f"Could not load full page {page_id}: {e}")
            retrieved_docs = list(full_pages.values())

        # answer_placeholder = st.empty()
        # full_answer = ""

        # for chunk in prompt_ai_stream(retrieved_docs, question):
        #     full_answer += chunk
        #     answer_placeholder.markdown(full_answer + "▌")

        # # Remove cursor after streaming is complete
        # answer_placeholder.markdown(full_answer)

        st.write_stream(prompt_ai_stream(retrieved_docs, question))

        st.markdown(f"### {_('Source Documents')}")

        # Create dataframe with source document chunks
        source_data = []
        for i, doc in enumerate(retrieved_docs, 1):
            metadata = doc.metadata
            # Create URL from page_id if available
            page_id = metadata.get("page_id")
            url = ""
            if page_id:
                try:
                    # Build URL from page_id
                    page = CollectivePage.get_from_page_id(page_id)
                    url = page.url or ""
                except Exception as e:
                    logger.warning(f"Could not load page {page_id} for URL: {e}")

            source_data.append(
                {
                    "#": i,
                    _("Title"): metadata.get("title", "N/A"),
                    _(
                        "Chunk"
                    ): f"{metadata.get('chunk_index', 0) + 1}/{metadata.get('total_chunks', 1)}",
                    _("Content"): doc.page_content,
                    _("Page ID"): page_id or "N/A",
                    _("URL"): url,
                }
            )

        st.dataframe(
            source_data,
            column_config={
                "#": st.column_config.NumberColumn("#", width="small"),
                _("Title"): st.column_config.TextColumn(_("Title"), width="medium"),
                _("Chunk"): st.column_config.TextColumn(_("Chunk"), width="small"),
                _("Content"): st.column_config.TextColumn(_("Content"), width="large"),
                _("Page ID"): st.column_config.TextColumn(_("Page ID"), width="small"),
                _("URL"): st.column_config.LinkColumn(
                    _("Link"), display_text=_("Open page"), width="small"
                ),
            },
            width="content",
            hide_index=True,
        )
    else:
        st.warning(_("No matching pages found."))
