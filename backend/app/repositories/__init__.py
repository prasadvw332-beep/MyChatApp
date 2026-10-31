"""Database access, one module per table group. The only code that queries PostgreSQL tables:

    chat_store.py   chats + messages (every function takes the owner user_id)
    doc_store.py    document metadata
    user_store.py   accounts
    usage_store.py  LLM token usage"""
