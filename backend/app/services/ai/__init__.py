"""LEDGR Intelligence.

An AI assistant layered on top of the deterministic ledger, never a
replacement for it.

    React  ->  FastAPI  ->  AI service  ->  LLM provider
                    \\-> read-only ledger tools -> existing services -> PostgreSQL

Nothing in this package holds a database session across the wire to a model,
executes SQL it received from a model, or calls `services.posting` directly.
A transaction proposal is a plain dict the *caller* (the API route) validates
with the same `TransactionCreate` schema the real posting endpoint uses, and
posting still happens only through `POST /transactions` - the AI layer never
gets a shortcut around it.
"""
