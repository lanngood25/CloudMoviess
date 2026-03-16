"""
CloudMovies — AI Assistant Backend (Groq)
Endpoint: POST /api/ai/chat
Dual API key with automatic fallback on rate limit.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("cloudmovies.ai")

# ── Groq Dual API Keys ────────────────────────────────────────────────────────
# Set these as environment variables in Railway:
#   GROQ_API_KEY_1=gsk_xxxx...
#   GROQ_API_KEY_2=gsk_yyyy...
GROQ_API_KEY_1 = os.getenv("GROQ_API_KEY_1", "")
GROQ_API_KEY_2 = os.getenv("GROQ_API_KEY_2", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

AI_SYSTEM_PROMPT = """Kamu adalah CineAI, asisten film yang ramah dan seru di website CloudMovies milik LannGood.
Tugasmu:
- Rekomendasikan film/series berdasarkan mood, genre, atau preferensi user
- Ceritakan sinopsis singkat film yang ditanyakan
- Bantu user menemukan film yang cocok
- Jawab pertanyaan seputar film, aktor, sutradara, dll

Aturan:
- Jawab dalam Bahasa Indonesia yang santai dan friendly
- Kalau menyebut judul film, tulis dalam format **Nama Film**
- Maksimal 4 kalimat per jawaban, jangan terlalu panjang
- Kalau user bilang lagi nonton sesuatu, kamu tau konteksnya"""

# ── Router ────────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/api/ai", tags=["AI"])


class Message(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    current_movie: Optional[str] = None  # title of movie user is viewing


class ChatResponse(BaseModel):
    reply: str
    key_used: int  # 1 or 2 (for debugging)


# ── Groq Chat Call ────────────────────────────────────────────────────────────
async def call_groq(api_key: str, messages: list[dict]) -> str:
    import httpx

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.8,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(GROQ_API_URL, json=payload, headers=headers)
        if r.status_code == 429:
            raise RateLimitError("Rate limited")
        if r.status_code == 401:
            raise ValueError("Invalid API key")
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]


class RateLimitError(Exception):
    pass


@router.post("/chat", response_model=ChatResponse)
async def ai_chat(req: ChatRequest):
    if not GROQ_API_KEY_1 and not GROQ_API_KEY_2:
        raise HTTPException(
            503, "AI service not configured. Set GROQ_API_KEY_1 in Railway variables."
        )

    # Build messages with system prompt
    system = AI_SYSTEM_PROMPT
    if req.current_movie:
        system += f"\n\nUser saat ini sedang melihat film: {req.current_movie}"

    messages = [{"role": "system", "content": system}]
    for m in req.messages[-10:]:  # last 10 messages for context
        messages.append({"role": m.role, "content": m.content})

    # Try API key 1 first, fallback to key 2 on rate limit
    keys = [(GROQ_API_KEY_1, 1), (GROQ_API_KEY_2, 2)]
    keys = [(k, n) for k, n in keys if k]  # filter empty keys

    last_error = None
    for api_key, key_num in keys:
        try:
            reply = await call_groq(api_key, messages)
            logger.info(f"AI response OK (key {key_num})")
            return ChatResponse(reply=reply, key_used=key_num)
        except RateLimitError:
            logger.warning(f"Rate limit on key {key_num}, trying next key...")
            last_error = f"Rate limit on key {key_num}"
            continue
        except Exception as e:
            logger.error(f"AI error (key {key_num}): {e}")
            last_error = str(e)
            continue

    raise HTTPException(429, f"All API keys exhausted: {last_error}")
