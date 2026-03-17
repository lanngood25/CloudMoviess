from __future__ import annotations

"""
CloudMovies — IPTV Router
Proxies requests to iptv-org public API (no API key needed).

Endpoints:
  GET /api/iptv/countries                → list countries with channel count
  GET /api/iptv/channels?country=ID      → channels for a country
  GET /api/iptv/streams?country=ID       → streams for a country
  GET /api/iptv/channels-with-streams?country=ID  → combined (main frontend endpoint)
  POST /api/iptv/cache/clear             → clear in-process cache
"""

import asyncio
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("cloudmovies.iptv")

router = APIRouter(prefix="/api/iptv", tags=["IPTV"])

IPTV_BASE = "https://iptv-org.github.io/api"

# ── Simple in-process cache (resets on server restart) ─────────────────────
_cache: dict = {}


async def _fetch(path: str) -> list:
    """Fetch JSON from iptv-org with basic in-memory caching."""
    if path in _cache:
        return _cache[path]

    url = f"{IPTV_BASE}/{path}"
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.get(url, headers={"User-Agent": "CloudMovies/1.0"})
            r.raise_for_status()
            data = r.json()
            _cache[path] = data
            logger.info(f"IPTV cached {path}: {len(data)} items")
            return data
    except Exception as e:
        logger.error(f"IPTV fetch error [{url}]: {e}")
        raise HTTPException(502, f"Failed to fetch IPTV data: {e}")


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.get("/countries")
async def get_countries():
    """All countries that have at least 1 channel, sorted by name."""
    countries = await _fetch("countries.json")
    result = [c for c in countries if c.get("channels", 0) > 0]
    result.sort(key=lambda c: c.get("name", ""))
    return result


@router.get("/channels")
async def get_channels(
    country: Optional[str] = Query(
        None, description="ISO 3166-1 alpha-2 code, e.g. ID"
    ),
):
    """All non-closed channels, optionally filtered by country."""
    channels = await _fetch("channels.json")
    result = [c for c in channels if not c.get("closed", False)]
    if country:
        code = country.upper()
        result = [c for c in result if c.get("country", "").upper() == code]
    return result


@router.get("/streams")
async def get_streams(
    country: Optional[str] = Query(None, description="ISO 3166-1 alpha-2 code"),
):
    """All streams with a valid URL, optionally filtered by country."""
    streams = await _fetch("streams.json")
    result = [s for s in streams if s.get("url") and s.get("channel")]

    if country:
        channels = await _fetch("channels.json")
        code = country.upper()
        ids = {
            c["id"]
            for c in channels
            if c.get("country", "").upper() == code and not c.get("closed", False)
        }
        result = [s for s in result if s.get("channel") in ids]

    return result


@router.get("/channels-with-streams")
async def get_channels_with_streams(
    country: str = Query(..., description="ISO 3166-1 alpha-2 code, e.g. ID"),
):
    """
    Main frontend endpoint.
    Returns channels for a country that have an active stream URL.
    Each item includes: id, name, logo, country, categories, languages, stream_url
    """
    code = country.upper()

    # Fetch in parallel
    channels, streams = await asyncio.gather(
        _fetch("channels.json"),
        _fetch("streams.json"),
    )

    # Country channels (non-closed)
    country_channels = {
        c["id"]: c
        for c in channels
        if c.get("country", "").upper() == code and not c.get("closed", False)
    }

    # First stream URL per channel id
    stream_map: dict[str, str] = {}
    for s in streams:
        ch_id = s.get("channel")
        url = s.get("url")
        if ch_id and url and ch_id not in stream_map:
            stream_map[ch_id] = url

    # Combine
    result = []
    for ch_id, ch in country_channels.items():
        stream_url = stream_map.get(ch_id)
        if not stream_url:
            continue
        result.append(
            {
                "id": ch_id,
                "name": ch.get("name", ""),
                "logo": ch.get("logo", ""),
                "country": ch.get("country", ""),
                "categories": ch.get("categories", []),
                "languages": ch.get("languages", []),
                "stream_url": stream_url,
            }
        )

    result.sort(key=lambda c: c["name"].lower())
    logger.info(f"IPTV {code}: {len(result)} channels with streams")
    return result


@router.post("/cache/clear")
async def clear_cache():
    """Force re-fetch from iptv-org on next request."""
    _cache.clear()
    return {"status": "ok", "message": "IPTV cache cleared"}
