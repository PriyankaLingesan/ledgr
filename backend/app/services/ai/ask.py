"""Ask LEDGR: a read-only, tool-grounded question-answering assistant.

The model never receives a database connection and never receives a SQL
string - it can only request one of the fixed tools in
`app.services.ai.tools`, the backend executes exactly that call against the
real ledger, and the JSON result is fed back as the next message. The loop
is capped at `settings.ai_max_tool_iterations` round trips so one question
cannot turn into unbounded provider calls.
"""

import json

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AIUnavailable
from app.schemas.ai import AskLedgerOut
from app.services.ai import provider as ai_provider
from app.services.ai.provider import AIMessage
from app.services.ai.tools import TOOL_SPECS, run_tool

_SYSTEM_PROMPT = """You are LEDGR Intelligence, answering questions about a real double-entry \
ledger using only the tools provided. Every figure in your answer must come from a tool result - \
never estimate, round suggestively, or invent a number. If the tools don't have the answer, say \
so plainly rather than guessing. Keep answers to two or three sentences. Amounts from tools \
already include their currency code; state them exactly as given."""


def ask_ledger(db: Session, question: str) -> AskLedgerOut:
    provider = ai_provider.get_provider()
    if provider is None:
        raise AIUnavailable("AI features are unavailable because no AI provider is configured")

    messages: list[AIMessage] = [AIMessage(role="user", content=question)]
    tools_used: list[str] = []

    for _ in range(settings.ai_max_tool_iterations):
        completion = provider.complete(
            system=_SYSTEM_PROMPT, messages=messages, tools=TOOL_SPECS, max_tokens=500
        )
        if not completion.wants_tool_calls:
            return AskLedgerOut(
                answer=completion.text or "I don't have an answer for that.",
                tools_used=tools_used,
            )

        messages.append(
            AIMessage(role="assistant", content=completion.text, tool_calls=completion.tool_calls)
        )
        for call in completion.tool_calls:
            tools_used.append(call.name)
            result = run_tool(db, call.name, call.arguments)
            messages.append(
                AIMessage(
                    role="tool",
                    tool_call_id=call.id,
                    tool_name=call.name,
                    content=json.dumps(result, default=str),
                )
            )

    return AskLedgerOut(
        answer=(
            "I gathered some information but couldn't finish forming an answer in time. "
            "Try a more specific question."
        ),
        tools_used=tools_used,
    )
