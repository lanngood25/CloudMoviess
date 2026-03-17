from __future__ import annotations

"""
CloudMovies Backend — FastAPI + moviebox-api
Run: uvicorn backend:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import logging
import os
from urllib.parse import quote
from contextlib import asynccontextmanager
from typing import Optional, AsyncGenerator

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel

from moviebox_api.core import HotMoviesAndTVSeries, Search, SearchSuggestion, Trending
from moviebox_api.constants import SubjectType
from moviebox_api.requests import Session
from moviebox_api.helpers import get_absolute_url

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cloudmovies")


async def fetch_with_retry(coro_factory, retries=3, base_delay=1.5):
    """Retry a coroutine on 429 with exponential backoff."""
    for attempt in range(retries):
        try:
            return await coro_factory()
        except Exception as e:
            if "429" in str(e) and attempt < retries - 1:
                wait = base_delay * (2**attempt)
                logger.warning(
                    f"429 rate limit, retrying in {wait:.1f}s (attempt {attempt + 1}/{retries})"
                )
                await asyncio.sleep(wait)
            else:
                raise
    raise RuntimeError("Max retries exceeded")


# ── Shared session ─────────────────────────────────────────────────────────────
_session: Optional[Session] = None

MOVIEBOX_HOST = "https://h5.aoneroom.com"

STREAM_URL = f"{MOVIEBOX_HOST}/wefeed-h5-bff/web/subject/play"
DOWNLOAD_URL = f"{MOVIEBOX_HOST}/wefeed-h5-bff/web/subject/download"


async def get_session() -> Session:
    global _session
    if _session is None:
        _session = Session()
        await _session.ensure_cookies_are_assigned()
        logger.info("MovieBox session initialised ✓")
    return _session


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_session()
    yield
    if _session:
        await _session._client.aclose()


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="CloudMovies API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Type"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(BASE_DIR, "index.html")

# Include AI router
from ai_router import router as ai_router
from iptv_router import router as iptv_router

app.include_router(ai_router)
app.include_router(iptv_router)


@app.get("/", response_class=HTMLResponse)
async def root():
    with open(INDEX_HTML, encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ── Helpers ────────────────────────────────────────────────────────────────────
def item_to_dict(item) -> dict:
    try:
        d = item.model_dump(mode="json") if hasattr(item, "model_dump") else vars(item)
    except Exception:
        d = {}
    for k, v in list(d.items()):
        if hasattr(v, "__str__") and not isinstance(
            v, (str, int, float, bool, list, dict, type(None))
        ):
            d[k] = str(v)
    return d


def items_list(items) -> list[dict]:
    return [item_to_dict(i) for i in items]


# ── Routes ─────────────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health():
    return {"status": "ok", "message": "CloudMovies backend is running 🎬"}


@app.get("/api/trending")
async def trending(page: int = 0, per_page: int = 24):
    try:
        session = await get_session()
        t = Trending(session, page=page, per_page=per_page)
        model = await t.get_content_model()
        return {
            "items": items_list(model.subjectList),
            "pager": model.pager.model_dump(),
        }
    except Exception as e:
        logger.error(f"trending error: {e}")
        raise HTTPException(500, str(e))


class SearchBody(BaseModel):
    keyword: str = ""
    subject_type: int = 0
    page: int = 1
    per_page: int = 24


@app.post("/api/search")
async def search(body: SearchBody):
    try:
        session = await get_session()
        stype = SubjectType(body.subject_type)
        s = Search(
            session,
            query=body.keyword,
            subject_type=stype,
            page=body.page,
            per_page=body.per_page,
        )
        model = await s.get_content_model()
        return {"items": items_list(model.items), "pager": model.pager.model_dump()}
    except Exception as e:
        err_str = str(e).lower()
        # Return empty result instead of 500 when keyword yields no results
        if "empty" in err_str or "no result" in err_str or "not found" in err_str:
            logger.warning(f"search empty [{body.keyword}]: {e}")
            return {
                "items": [],
                "pager": {"total": 0, "page": body.page, "perPage": body.per_page},
            }
        logger.error(f"search error: {e}")
        raise HTTPException(500, str(e))


@app.get("/api/browse")
async def browse(
    subject_type: int = Query(default=0, description="0=all, 1=movie, 2=series"),
    page: int = Query(default=0),
    per_page: int = Query(default=48),
):
    """
    Browse content by type using trending endpoint.
    Fetches multiple pages and optionally filters by subjectType.
    Returns up to per_page items from page N.
    """
    try:
        session = await get_session()
        collected = []
        seen = set()
        # Fetch enough trending pages to fill request
        for p in range(page, page + 6):
            try:
                t = Trending(session, page=p, per_page=48)
                model = await t.get_content_model()
                items = model.subjectList or []
                if not items:
                    break
                for it in items:
                    sid = getattr(it, "subjectId", None) or str(it)
                    if sid in seen:
                        continue
                    seen.add(sid)
                    if (
                        subject_type == 0
                        or getattr(it, "subjectType", 0) == subject_type
                    ):
                        collected.append(it)
                if len(collected) >= per_page:
                    break
            except Exception:
                break
        return {
            "items": items_list(collected[:per_page]),
            "page": page,
            "per_page": per_page,
            "has_more": len(collected) >= per_page,
        }
    except Exception as e:
        logger.error(f"browse error: {e}")
        raise HTTPException(500, str(e))


@app.get("/api/suggest")
async def suggest(q: str = Query(..., min_length=1)):
    try:
        session = await get_session()
        ss = SearchSuggestion(session)
        data = await ss.get_content(q)
        return data
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/hot")
async def hot():
    try:
        session = await get_session()
        h = HotMoviesAndTVSeries(session)
        model = await h.get_content_model()
        return {
            "movies": items_list(model.movies),
            "tv_series": items_list(model.tv_series),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Stream ─────────────────────────────────────────────────────────────────────


@app.get("/api/stream/{subject_id}")
async def stream_info(
    subject_id: str,
    detail_path: str = Query(default=""),
    subject_type: int = Query(default=1),
    se: int = 0,
    ep: int = 0,
):
    try:
        session = await get_session()
        referer = f"{MOVIEBOX_HOST}/movies/{detail_path}"

        actual_se = se if subject_type == 2 else 0
        actual_ep = ep if subject_type == 2 else 0

        # Fetch stream with retry on 429
        try:
            stream_content = await fetch_with_retry(
                lambda: session.get_with_cookies_from_api(
                    url=STREAM_URL,
                    params={"subjectId": subject_id, "se": se, "ep": ep},
                    headers={"Referer": referer},
                )
            )
        except Exception as e:
            logger.error(f"stream fetch error: {e}")
            stream_content = {}

        await asyncio.sleep(0.3)

        # Fetch captions with retry on 429
        try:
            dl_content = await fetch_with_retry(
                lambda: session.get_with_cookies_from_api(
                    url=DOWNLOAD_URL,
                    params={"subjectId": subject_id, "se": actual_se, "ep": actual_ep},
                    headers={"Referer": referer},
                )
            )
        except Exception as e:
            logger.warning(f"caption fetch error: {e}")
            dl_content = {}

        streams = []
        for s in stream_content.get("streams", []):
            raw_url = str(s.get("url", ""))
            res = s.get("resolutions", s.get("resolution", 0))
            streams.append(
                {
                    "url": f"/api/video-proxy?url={quote(raw_url, safe='')}",
                    "direct_url": raw_url,
                    "resolution": res,
                    "size": s.get("size", 0),
                }
            )
        streams.sort(key=lambda x: x["resolution"], reverse=True)

        hls = []
        for h in stream_content.get("hls", []):
            raw_url = str(h.get("url", ""))
            hls.append(
                {
                    "url": f"/api/video-proxy?url={quote(raw_url, safe='')}",
                    "direct_url": raw_url,
                    "resolution": h.get("resolution", 0),
                }
            )

        captions = []
        for c in dl_content.get("captions", []):
            raw_sub_url = str(c.get("url", ""))
            lan = c.get("lan", "")
            lan_name = c.get("lanName", "") or lan
            captions.append(
                {
                    "id": c.get("id", ""),
                    "lan": lan,
                    "lanName": lan_name,
                    "url": f"/api/subtitle-proxy?url={quote(raw_sub_url, safe='')}",
                    "size": c.get("size", 0),
                    "delay": c.get("delay", 0),
                }
            )

        logger.info(
            f"stream [{subject_id}] se={se} ep={ep}: {len(streams)} streams, {len(captions)} subs ({[c['lan'] for c in captions]})"
        )
        return {
            "streams": streams,
            "captions": captions,
            "hls": hls,
            "limited": stream_content.get("limited", False),
        }

    except Exception as e:
        logger.error(f"stream error [{subject_id}]: {e}")
        raise HTTPException(500, str(e))


# ── Download ───────────────────────────────────────────────────────────────────


@app.get("/api/download/{subject_id}")
async def download_info(
    subject_id: str,
    detail_path: str = Query(default=""),
    subject_type: int = Query(default=1),
    se: int = 0,
    ep: int = 0,
):
    try:
        session = await get_session()

        actual_se = se if subject_type == 2 else 0
        actual_ep = ep if subject_type == 2 else 0

        referer = f"{MOVIEBOX_HOST}/movies/{detail_path}"
        content = await session.get_with_cookies_from_api(
            url=DOWNLOAD_URL,
            params={"subjectId": subject_id, "se": actual_se, "ep": actual_ep},
            headers={"Referer": referer},
        )

        downloads = []
        for f in content.get("downloads", []):
            raw_url = str(f.get("url", ""))
            downloads.append(
                {
                    "id": f.get("id", ""),
                    "url": f"/api/video-proxy?url={quote(raw_url, safe='')}",
                    "direct_url": raw_url,
                    "resolution": f.get("resolution", 0),
                    "size": f.get("size", 0),
                }
            )
        downloads.sort(key=lambda x: x["resolution"], reverse=True)

        captions = []
        for c in content.get("captions", []):
            captions.append(
                {
                    "id": c.get("id", ""),
                    "lan": c.get("lan", ""),
                    "lanName": c.get("lanName", ""),
                    "url": str(c.get("url", "")),
                    "size": c.get("size", 0),
                }
            )

        return {
            "downloads": downloads,
            "captions": captions,
            "limited": content.get("limited", False),
        }

    except Exception as e:
        logger.error(f"download error [{subject_id}]: {e}")
        raise HTTPException(500, str(e))


# ── Video Proxy — bypasses CDN CORS ───────────────────────────────────────────

VIDEO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Referer": "https://fmoviesunblocked.net/",
    "Accept": "*/*",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}


@app.get("/api/video-proxy")
async def video_proxy(request: Request, url: str = Query(...)):
    """Streams CDN video through localhost — fixes browser CORS. Supports Range for seeking."""
    if not url.startswith("http"):
        raise HTTPException(400, "Invalid URL")

    req_headers = dict(VIDEO_HEADERS)
    if rng := request.headers.get("range"):
        req_headers["Range"] = rng

    try:
        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(connect=15, read=120, write=15, pool=15),
        )
        upstream = await client.send(
            client.build_request("GET", url, headers=req_headers),
            stream=True,
        )

        ct = upstream.headers.get("content-type", "video/mp4")
        if url.endswith(".srt") or url.endswith(".vtt"):
            ct = "text/plain; charset=utf-8"

        resp_headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store",
        }
        for h in ["Content-Length", "Content-Range", "Content-Type"]:
            val = upstream.headers.get(h) or upstream.headers.get(h.lower())
            if val:
                resp_headers[h] = val

        async def streamer() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in upstream.aiter_bytes(65536):
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            streamer(),
            status_code=upstream.status_code,
            media_type=ct,
            headers=resp_headers,
        )

    except Exception as e:
        logger.error(f"video-proxy error: {e}")
        raise HTTPException(502, str(e))


# ── Subtitle Proxy ─────────────────────────────────────────────────────────────


@app.get("/api/subtitle-proxy")
async def subtitle_proxy(url: str = Query(...)):
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
        return StreamingResponse(
            iter([r.content]),
            media_type="text/plain; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*"},
        )
    except Exception as e:
        raise HTTPException(500, str(e))


if __name__ == "__main__":
    import uvicorn
    import os

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("backend:app", host="0.0.0.0", port=port)
