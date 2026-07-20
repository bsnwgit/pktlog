"""
POST /api/ai/chat — Claude AI assistant endpoint.
Sends current view context + user question to the Anthropic API.
Requires a valid API key in settings (anthropic_api_key).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import CurrentUser

router = APIRouter()
log = logging.getLogger("pktlog.ai")

SYSTEM_PROMPT = """You are a log analysis assistant integrated into pktLog, a syslog collection
and analysis platform. Your role is to help engineers interpret log volume, syslog severity
levels and facility codes, and event patterns, and to help troubleshoot log ingestion,
parsing, and collector issues.

You will receive structured log context (recent events, collector status, alert summaries)
alongside the user's question. Analyze the data and provide clear, concise answers.

Guidelines:
- Be specific and reference the actual data provided when relevant
- Flag error spikes, unusual event patterns, or potential issues you notice
- Suggest investigation steps when appropriate
- Keep responses focused — users are busy engineers
- Use plain text; avoid markdown headers in responses (inline bold is fine)"""

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class ChatRequest(BaseModel):
    question: str
    context: dict[str, Any] = {}  # Optional view context passed by the frontend


class ChatResponse(BaseModel):
    answer: str
    tokens_used: int = 0


async def _get_setting(db: aiosqlite.Connection, key: str) -> Any:
    async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
        row = await cur.fetchone()
    return json.loads(row[0]) if row else None


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    _: CurrentUser,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Send a question + log context to Claude and return the answer."""
    api_key = await _get_setting(db, "anthropic_api_key")
    if not api_key or api_key == "••••••••":
        raise HTTPException(
            status_code=503,
            detail="AI assistant not configured. Add your Anthropic API key in Settings → AI Assistant.",
        )

    model = await _get_setting(db, "ai_model") or DEFAULT_MODEL

    context_str = json.dumps(body.context, indent=2) if body.context else "(No context provided)"
    user_message = f"Log Context:\n{context_str}\n\nQuestion: {body.question}"

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        answer = response.content[0].text
        tokens = response.usage.input_tokens + response.usage.output_tokens
        return ChatResponse(answer=answer, tokens_used=tokens)

    except Exception as e:
        log.error(f"AI chat error: {e}")
        if "authentication" in str(e).lower() or "api_key" in str(e).lower():
            raise HTTPException(status_code=503, detail="Invalid Anthropic API key. Check Settings → AI Assistant.")
        raise HTTPException(status_code=502, detail=f"AI service error: {str(e)[:200]}")
