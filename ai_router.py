from __future__ import annotations

"""
CloudMovies — CineAI Router
Powered by Groq API + llama-3.3-70b-versatile
Endpoint: POST /api/ai/chat
"""

import os
import logging
from typing import Optional
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("cloudmovies.ai")
router = APIRouter(prefix="/api/ai", tags=["AI"])

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# Dual-key fallback untuk handle rate limit
GROQ_KEYS = [
    k
    for k in [
        os.getenv("GROQ_API_KEY_1"),
        os.getenv("GROQ_API_KEY_2"),
        os.getenv("GROQ_API_KEY"),
    ]
    if k
]

SYSTEM_PROMPT = """Kamu adalah CineAI — asisten film, serial TV, dan hiburan yang cerdas, gaul, dan asik banget. Kamu ada di dalam CloudMovies, sebuah platform streaming film dan kamu dibuat oleh LannGood.

Kepribadian kamu:
- Ramah, santai, dan asyik diajak ngobrol — pakai bahasa Indonesia yang natural dan gaul tapi tetap mudah dipahami
- Adaptif: kalau user ngomong formal, kamu ikut formal. Kalau santai dan kasual, kamu juga santai
- Berselera humor tapi tetap informatif
- Antusias banget soal film dan series — kamu genuinely excited kalau ngomongin konten bagus

Kemampuan kamu:
- Rekomendasiin film/series berdasarkan mood, genre, atau film yang udah ditonton user
- Jelasin sinopsis, plot, karakter, sutradara, pemeran, dan fakta menarik tentang film/series apapun
- Jelasin perbedaan genre, sutradara, atau era film
- Bantu user milih antara beberapa pilihan film
- Kasih tau rating, penghargaan, dan pencapaian sebuah film/series
- Diskusi soal teori, easter egg, dan detail tersembunyi di film
- Rekomendasiin film serupa berdasarkan yang user suka
- Kalau user lagi nonton sesuatu (info dikirim dari context), kamu tahu dan bisa komentar soal itu

Format jawaban:
- Pakai **nama film/series** dengan bold supaya kelihatan jelas
- Kalau rekomendasiin beberapa film, beri list yang rapi
- Jangan terlalu panjang — jawab yang relevan dan padat
- Boleh pakai emoji yang relevan sesekali
- Kalau user tanya sesuatu di luar topik film/hiburan, tetap bantu tapi arahkan balik ke topik film

Ingat: Kamu ada di dalam platform streaming, jadi user bisa langsung cari film yang kamu rekomendasiin di sini."""


class Message(BaseModel):
    role: str
    content: str


class ChatBody(BaseModel):
    messages: list[Message]
    current_movie: Optional[str] = None


async def call_groq(messages: list[dict], key: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": messages,
                "max_tokens": 600,
                "temperature": 0.85,
                "top_p": 0.9,
            },
        )
        if resp.status_code == 429:
            raise httpx.HTTPStatusError(
                "rate_limit", request=resp.request, response=resp
            )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


@router.post("/chat")
async def chat(body: ChatBody):
    if not GROQ_KEYS:
        raise HTTPException(503, "GROQ_API_KEY not configured")

    # Build messages list
    system = SYSTEM_PROMPT
    if body.current_movie:
        system += f"\n\nContext: User saat ini sedang melihat/menonton **{body.current_movie}** di CloudMovies."

    messages = [{"role": "system", "content": system}]
    for m in body.messages[-14:]:  # keep last 14 turns for memory
        messages.append({"role": m.role, "content": m.content})

    # Try each key, fallback on 429
    last_err = None
    for key in GROQ_KEYS:
        try:
            reply = await call_groq(messages, key)
            return {"reply": reply}
        except httpx.HTTPStatusError as e:
            if "rate_limit" in str(e) or e.response.status_code == 429:
                last_err = e
                logger.warning("Groq key rate limited, trying next key...")
                continue
            logger.error(f"Groq API error: {e}")
            raise HTTPException(502, f"AI error: {e}")
        except Exception as e:
            logger.error(f"Groq call failed: {e}")
            raise HTTPException(502, f"AI error: {e}")

    raise HTTPException(429, "AI sedang sibuk, coba lagi sebentar!")
