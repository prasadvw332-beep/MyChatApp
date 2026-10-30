"""Retrieval-augmented generation: the knowledge base and answering from it.

    chunking.py        file -> text -> section-aware chunks
    knowledge_base.py  index / retrieve / delete documents (vectors via services/vector_store.py)
    answer.py          retrieved chunks + chat history -> messages for the chat model

Used by the chat API (app/api/routes/chat.py), the ingestion workers (app/ingestion/) and the evals.
An agent layer can call knowledge_base.retrieve() as one of its tools.
"""
