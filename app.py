from typing import List, cast

import streamlit as st
from google import genai

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.collective_page import (
    CollectivePage,
    get_collective_page_collection,
)
from lib.settings import _, settings


def prompt_ai(pages: List[CollectivePage], question: str) -> str | None:
    """Ask AI a question based on collective pages context."""
    context = "\n\n".join(
        [
            f"Titel: {p.title}\nDatum: {p.formatted_timestamp}\nInhalt: {p.content}"
            for p in pages
            if p.content
        ]
    )

    prompt = f"""Du bist ein hilfreicher Assistent, der Informationen aus Kollektiv-Seiten zusammenfasst und Fragen dazu beantwortet.
    Nutze den folgenden Kontext, um die Frage zu beantworten. Wenn die Information nicht im Kontext vorhanden ist, antworte mit "Die Information ist nicht verfügbar".

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


# Streamlit app starts here
title = _("{common_name} Dashboard").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="🏡", layout="wide")

menu()

db = couchdb()


st.title(title)

# AI Question Section
st.subheader("🤖 " + _("Ask a Question") + " 🤖")

collective_collection = get_collective_page_collection()

col1, col2 = st.columns([3, 1])
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

if question:
    results = collective_collection.query(
        query_texts=[question],
        n_results=num_results,
    )

    result_ids = results["ids"][0]
    matching_pages = [
        p
        for p in cast(
            List[CollectivePage],
            CollectivePage.get_all(limit=100, sort=[{"ocs.timestamp": "desc"}]),
        )
        if p.id in result_ids
    ]

    if matching_pages:
        with st.spinner(_("Thinking...")):
            answer = prompt_ai(matching_pages, question)
        st.markdown(f"### {_('Answer')}:")
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
