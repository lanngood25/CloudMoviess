from __future__ import annotations

"""
CloudMovies — LK21 Scraper (Movies Only)
Search LK21 and extract stream URLs from movie pages.
"""

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("cloudmovies.lk21")

LK21_URL = "https://tv9.lk21official.cc"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.7",
    "Referer": LK21_URL + "/",
}


def _client(referer=None):
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    return httpx.AsyncClient(
        headers=h,
        follow_redirects=True,
        timeout=httpx.Timeout(connect=12, read=25, write=10, pool=10),
    )


def _extract_year(text):
    m = re.search(r"(19|20)\d{2}", text)
    return m.group(0) if m else ""


def _extract_rating(text):
    m = re.search(r"(\d+\.?\d*)\s*/\s*10", text)
    if m:
        return float(m.group(1))
    m2 = re.search(r"(\d+\.\d+)", text)
    return float(m2.group(1)) if m2 else 0.0


# ── SEARCH ────────────────────────────────────────────────────────────────────


def _make_slug(title: str, year: str = "") -> str:
    """Convert title to LK21 URL slug, e.g. 'Battleship 2012' → 'battleship-2012'"""
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug).strip("-")
    if year and year not in slug:
        slug = f"{slug}-{year}"
    return slug


async def _fetch_lk21_page(url: str) -> Optional[dict]:
    """Fetch LK21 page, return metadata if it's a real movie page."""
    try:
        async with _client() as client:
            r = await client.get(url, timeout=10)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")

            # Reject ONLY if it's homepage/search page (not a movie page)
            # LK21 puts brand in title like "Nonton Battleship (2012) Sub Indo di LK21"
            # so we CANNOT reject on "lk21" keyword
            reject_homepage = [
                "streaming film gratis",
                "nonton film online",
                "layarkaca21 | nonton",
            ]
            page_title_tag = soup.find("title")
            page_title_str = page_title_tag.get_text() if page_title_tag else ""
            if any(p in page_title_str.lower() for p in reject_homepage):
                return None

            # Try multiple title selectors — LK21 uses different themes
            title_el = (
                soup.select_one("h1.entry-title")
                or soup.select_one("h1.judul-film")
                or soup.select_one(".film-title h1")
                or soup.select_one(".gmr-title")
                or soup.select_one("h1")
            )
            if not title_el:
                return None

            raw_title = re.sub(r"\s+", " ", title_el.get_text()).strip()

            # Clean LK21 branding from title
            # e.g. "Nonton Battleship (2012) Sub Indo" → "Battleship (2012)"
            clean = raw_title
            for prefix in ["nonton ", "download ", "streaming "]:
                if clean.lower().startswith(prefix):
                    clean = clean[len(prefix) :]
            for suffix in [
                " sub indo",
                " subtitle indonesia",
                " full movie",
                " online",
                " di lk21",
                " di layarkaca21",
                " gratis",
            ]:
                if clean.lower().endswith(suffix):
                    clean = clean[: -len(suffix)]
            # Remove year in parentheses from title for display
            # but keep it for releaseDate extraction
            clean = clean.strip()

            if len(clean) < 2:
                return None

            # Extract year from title like "Battleship (2012)" or from page
            year_from_title = re.search(r"\((\d{4})\)", clean)
            page_year = ""
            if year_from_title:
                page_year = year_from_title.group(1)
                # Remove year from display title
                clean = re.sub(r"\s*\(\d{4}\)\s*", " ", clean).strip()
            if not page_year:
                year_el = soup.select_one(".year, .gmr-movie-on, time, .released")
                page_year = _extract_year(
                    year_el.get_text() if year_el else ""
                ) or _extract_year(page_title_str)

            # Poster
            # Get poster from TMDB - most reliable source
            poster = await _get_tmdb_poster(clean, page_year)
            # Fallback to og:image if TMDB fails
            if not poster:
                og_img = soup.select_one('meta[property="og:image"]')
                if og_img:
                    poster = og_img.get("content", "")

            score_el = soup.select_one(
                ".imdb, .rating, [itemprop=ratingValue], .gmr-rating"
            )
            score = _extract_rating(score_el.get_text() if score_el else "")
            desc_el = soup.select_one(
                ".synopsis p, .entry-content p, [itemprop=description]"
            )
            desc = (
                re.sub(r"\s+", " ", desc_el.get_text()).strip()[:300] if desc_el else ""
            )

            logger.info(f"LK21 direct hit: {url} → {clean} ({page_year})")
            return {
                "subjectId": f"lk21_{abs(hash(url)) % 10**10}",
                "title": clean,
                "description": desc,
                "releaseDate": f"{page_year}-01-01" if page_year else "2000-01-01",
                "duration": 0,
                "genre": "Movie",
                "countryName": "",
                "imdbRatingValue": score,
                "subjectType": 1,
                "detailPath": "",
                "corner": "",
                "appointmentCnt": 0,
                "appointmentDate": "",
                "stafflist": None,
                "subtitles": "id",
                "hasResource": True,
                "source": "lk21",
                "sourceUrl": url,
                "sourceName": "LK21",
                "cover": {
                    "url": poster,
                    "thumbnail": poster,
                    "width": 0,
                    "height": 0,
                    "size": 0,
                    "format": "",
                    "blurHash": "",
                    "avgHueLight": "",
                    "avgHueDark": "",
                    "id": "0",
                },
            }
    except Exception as e:
        logger.debug(f"LK21 page miss {url}: {e}")
    return None


async def _try_direct_url(title: str, year: str) -> Optional[dict]:
    """
    Try a few smart URL variants for a movie title.
    Only tries slug WITH known year — no year brute force.
    """
    base_slug = _make_slug(title, "")

    # Only try if we have a year
    if year:
        url = f"{LK21_URL}/{base_slug}-{year}"
        result = await _fetch_lk21_page(url)
        if result:
            return result

    # Try without year
    url_bare = f"{LK21_URL}/{base_slug}"
    return await _fetch_lk21_page(url_bare)


def _clean_title_for_search(title: str) -> str:
    """Remove Indonesian localization suffixes from title for TMDB search."""
    clean = title
    for suffix in [
        " sub indo",
        " subtitle indonesia",
        " sub indonesia",
        " bahasa indonesia",
        " full movie",
        " online",
        " nonton",
        " streaming",
        " bluray",
        " blu-ray",
        " hdcam",
        " webrip",
    ]:
        if clean.lower().endswith(suffix):
            clean = clean[: -len(suffix)].strip()
    return clean.strip()


async def _get_tmdb_poster(title: str, year: str = "") -> str:
    """Fetch poster URL from TMDB."""
    try:
        # Clean title before searching
        clean = _clean_title_for_search(title)
        query = quote_plus(clean)
        yr_param = f"&year={year}" if year else ""
        url = f"https://api.themoviedb.org/3/search/movie?api_key=8265bd1679663a7ea12ac168da84d2e8&query={query}{yr_param}&language=id-ID"
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(url)
            if r.status_code == 200:
                data = r.json()
                results = data.get("results", [])
                if results:
                    path = results[0].get("poster_path", "")
                    if path:
                        return f"https://image.tmdb.org/t/p/w500{path}"
                # If no results with year, try without year
                if year and not results:
                    url2 = f"https://api.themoviedb.org/3/search/movie?api_key=8265bd1679663a7ea12ac168da84d2e8&query={query}&language=id-ID"
                    r2 = await c.get(url2)
                    if r2.status_code == 200:
                        data2 = r2.json()
                        results2 = data2.get("results", [])
                        if results2:
                            path = results2[0].get("poster_path", "")
                            if path:
                                return f"https://image.tmdb.org/t/p/w500{path}"
    except Exception as e:
        logger.debug(f"TMDB poster error: {e}")
    return ""


async def search_lk21_movies(query: str, max_results: int = 20) -> list:
    """Search LK21, return movies only (no series). Also tries page 2."""
    # Fetch page 1 and page 2 in parallel
    url1 = f"{LK21_URL}/?s={quote_plus(query)}"
    url2 = f"{LK21_URL}/page/2/?s={quote_plus(query)}"
    results = []
    all_html = []
    try:
        async with _client() as client:
            r1, r2 = await asyncio.gather(
                client.get(url1),
                client.get(url2),
                return_exceptions=True,
            )
        for r in [r1, r2]:
            if isinstance(r, Exception):
                continue
            if r.status_code == 200:
                all_html.append(r.text)

        if not all_html:
            return []

        # Also try direct URL for query as a title (e.g. "battleship 2012")
        year_match = re.search(r"(19|20)\d{2}", query)
        year = year_match.group(0) if year_match else ""
        title_part = re.sub(r"(19|20)\d{2}", "", query).strip()
        direct_task = _try_direct_url(title_part or query, year)

        # Also search LK21 with year appended if not already in query
        extra_search_urls = []
        if not year and query:
            # Try searching "query year" for common years (last 15 years)
            import datetime

            cur_year = datetime.datetime.now().year
            for y in range(cur_year, cur_year - 15, -1):
                extra_search_urls.append(
                    f"{LK21_URL}/?s={quote_plus(query + ' ' + str(y))}"
                )

        soup_pages = [BeautifulSoup(h, "html.parser") for h in all_html]

        cards = []
        for soup in soup_pages:
            page_cards = (
                soup.select("article.item")
                or soup.select(".movies-list article")
                or soup.select("article")
                or soup.select(".item")
            )
            cards.extend(page_cards)

        series_keywords = [
            "season",
            "episode",
            "s01",
            "s02",
            "eps",
            "the series",
            "complete series",
        ]

        for card in cards:
            if len(results) >= max_results:
                break
            try:
                title_el = card.select_one("h3, h2, .entry-title, .title")
                if not title_el:
                    continue
                title = re.sub(r"\s+", " ", title_el.get_text()).strip()
                if not title:
                    continue

                # Skip series
                title_lower = title.lower()
                if any(kw in title_lower for kw in series_keywords):
                    continue
                if re.search(r"\bS\d{2}E\d{2}\b", title, re.I):
                    continue

                link_el = card.select_one("a[href]")
                if not link_el:
                    continue
                page_url = link_el["href"]
                if not page_url.startswith("http"):
                    page_url = urljoin(LK21_URL, page_url)

                img_el = card.select_one("img")
                poster = ""
                if img_el:
                    # LK21 uses lazy loading - check all possible attrs
                    poster = (
                        img_el.get("data-src")
                        or img_el.get("data-lazy-src")
                        or img_el.get("data-original")
                        or img_el.get("data-lazy")
                        or img_el.get("src")
                        or ""
                    )
                    # Skip placeholder/spinner images
                    if poster and any(
                        skip in poster
                        for skip in [
                            "placeholder",
                            "spinner",
                            "loading",
                            "blank.gif",
                            "data:image",
                        ]
                    ):
                        poster = ""
                    if poster and poster.startswith("//"):
                        poster = "https:" + poster
                    # Also try background-image style

                year = _extract_year(card.get_text())
                score_el = card.select_one(".rating, .imdb, .score")
                score = _extract_rating(score_el.get_text() if score_el else "")
                genre_els = card.select(".genre a, .categories a, .cat a")
                genres = ", ".join(g.get_text(strip=True) for g in genre_els[:3])

                # Get poster from TMDB for accurate results
                if not poster:
                    poster = await _get_tmdb_poster(title, year)

                results.append(
                    {
                        "subjectId": f"lk21_{abs(hash(page_url)) % 10**10}",
                        "title": title,
                        "description": "",
                        "releaseDate": f"{year}-01-01" if year else "2000-01-01",
                        "duration": 0,
                        "genre": genres or "Movie",
                        "countryName": "",
                        "imdbRatingValue": score,
                        "subjectType": 1,
                        "detailPath": "",
                        "corner": "",
                        "appointmentCnt": 0,
                        "appointmentDate": "",
                        "stafflist": None,
                        "subtitles": "id",
                        "hasResource": True,
                        "source": "lk21",
                        "sourceUrl": page_url,
                        "sourceName": "LK21",
                        "cover": {
                            "url": poster,
                            "thumbnail": poster,
                            "width": 0,
                            "height": 0,
                            "size": 0,
                            "format": "",
                            "blurHash": "",
                            "avgHueLight": "",
                            "avgHueDark": "",
                            "id": "0",
                        },
                    }
                )
            except Exception as card_err:
                logger.debug(f"Card parse error: {card_err}")
                continue

        # Try direct URL lookup in parallel
        direct = await direct_task
        if direct:
            existing = {r["title"].lower() for r in results}
            if direct["title"].lower() not in existing:
                results.insert(0, direct)  # put at top

        logger.info(f"LK21 search '{query}': {len(results)} movies")
    except Exception as e:
        logger.error(f"LK21 search error: {e}")

    return results


# ── STREAM EXTRACTION ─────────────────────────────────────────────────────────


async def extract_lk21_stream(page_url: str) -> dict:
    """Visit LK21 movie page, find all embed/direct video URLs."""
    streams = []
    try:
        async with _client(referer=LK21_URL) as client:
            r = await client.get(page_url)
            if r.status_code != 200:
                return {"streams": [], "captions": []}
            soup = BeautifulSoup(r.text, "html.parser")
            html = r.text

        # 1. Direct mp4/m3u8 in page source
        direct = re.findall(r'https?://[^\s\'"<>]+\.(?:mp4|m3u8)[^\s\'"<>]*', html)
        for u in dict.fromkeys(direct):
            if any(skip in u for skip in ["thumbnail", "poster", ".css", "cdn.js"]):
                continue
            label = "1080p" if "1080" in u else "720p" if "720" in u else "HD"
            streams.append(
                {"url": u, "resolution": label, "label": label, "source": "direct"}
            )
            if len(streams) >= 3:
                break

        # 2. JS file/src/source keys
        js_urls = re.findall(
            r'(?:file|src|source)\s*:\s*["\']( https?://[^"\']+)["\']', html
        )
        for u in dict.fromkeys(js_urls):
            u = u.strip()
            if u and u not in {s["url"] for s in streams}:
                label = "1080p" if "1080" in u else "720p" if "720" in u else "HD"
                streams.append(
                    {"url": u, "resolution": label, "label": label, "source": "js"}
                )

        # 3. Iframes
        iframes = soup.select("iframe[src], iframe[data-src]")
        embed_urls = []
        skip_domains = ["google.com/maps", "facebook.com", "twitter.com", "youtube.com"]
        for iframe in iframes:
            src = iframe.get("src") or iframe.get("data-src", "")
            if src and not any(d in src for d in skip_domains):
                if not src.startswith("http"):
                    src = urljoin(page_url, src)
                embed_urls.append(src)
        # Also grep JS for playeriframe/stream URLs
        # grep JS for known stream domains handled by resolve_generic
        embed_urls = list(dict.fromkeys(embed_urls))

        # 4. Resolve embeds (max 4 to avoid rate limits)
        resolved = await asyncio.gather(
            *[_resolve_embed(eu, page_url) for eu in embed_urls[:4]],
            return_exceptions=True,
        )
        for res in resolved:
            if isinstance(res, list):
                streams.extend(res)

        # Deduplicate
        seen = set()
        unique = []
        for s in streams:
            if s["url"] not in seen:
                seen.add(s["url"])
                unique.append(s)

        logger.info(f"LK21 extract '{page_url}': {len(unique)} streams")
        return {"streams": unique[:6], "captions": []}

    except Exception as e:
        logger.error(f"LK21 extract error: {e}")
        return {"streams": [], "captions": []}


async def _resolve_embed(embed_url: str, referer: str) -> list:
    domain = urlparse(embed_url).netloc.lower()
    try:
        if "streamtape" in domain:
            return await _resolve_streamtape(embed_url)
        elif "dood" in domain:
            return await _resolve_doodstream(embed_url)
        elif "playeriframe" in domain:
            return await _resolve_playeriframe(embed_url, referer)
        else:
            return await _resolve_generic(embed_url, referer)
    except Exception as e:
        logger.debug(f"Embed resolve error {embed_url}: {e}")
        return []


async def _resolve_streamtape(url: str) -> list:
    try:
        async with _client(referer="https://streamtape.com") as c:
            r = await c.get(url, timeout=10)
            m = re.search(r"(streamtape\.com/get_video\?[^\s\"'<>]+)", r.text)
            if m:
                return [
                    {
                        "url": "https://" + m.group(1),
                        "resolution": "HD",
                        "label": "HD",
                        "source": "streamtape",
                    }
                ]
            m2 = re.search(
                r'(?:file|src)\s*[=:]\s*["\']( https?://[^"\']+)["\']', r.text
            )
            if m2:
                return [
                    {
                        "url": m2.group(1).strip(),
                        "resolution": "HD",
                        "label": "HD",
                        "source": "streamtape",
                    }
                ]
    except Exception as e:
        logger.debug(f"Streamtape error: {e}")
    return []


async def _resolve_doodstream(url: str) -> list:
    try:
        async with _client(referer="https://dood.to") as c:
            r = await c.get(url, timeout=10)
            m = re.search(r"/pass_md5/([^\s\"'<>&]+)", r.text)
            if m:
                pass_url = "https://dood.to/pass_md5/" + m.group(1)
                r2 = await c.get(pass_url, timeout=10)
                token_m = re.search(r"token=([^\s\"'&<>]+)", r.text)
                token = ("?token=" + token_m.group(1)) if token_m else ""
                video_url = r2.text.strip() + token
                if video_url.startswith("http"):
                    return [
                        {
                            "url": video_url,
                            "resolution": "HD",
                            "label": "HD",
                            "source": "dood",
                        }
                    ]
    except Exception as e:
        logger.debug(f"Doodstream error: {e}")
    return []


async def _resolve_generic(url: str, referer: str) -> list:
    try:
        async with _client(referer=referer) as c:
            r = await c.get(url, timeout=12)
            found = re.findall(r'https?://[^\s\'"<>]+\.(?:m3u8|mp4)[^\s\'"<>]*', r.text)
            streams = []
            for u in dict.fromkeys(found):
                if any(skip in u for skip in ["thumbnail", "poster", ".css"]):
                    continue
                label = "1080p" if "1080" in u else "720p" if "720" in u else "HD"
                streams.append(
                    {"url": u, "resolution": label, "label": label, "source": "embed"}
                )
                if len(streams) >= 3:
                    break
            return streams
    except Exception as e:
        logger.debug(f"Generic embed error: {e}")
    return []


async def _resolve_playeriframe(url: str, referer: str) -> list:
    """Resolve playeriframe.sbs used by LK21."""
    streams = []
    try:
        h = {**HEADERS, "Referer": referer}
        async with httpx.AsyncClient(headers=h, follow_redirects=True, timeout=15) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return []
            html = r.text

            # m3u8/mp4 direct
            found = re.findall(r'https?://[^\s\'"<>]+\.(?:m3u8|mp4)[^\s\'"<>]*', html)
            for u in dict.fromkeys(found):
                if any(s in u for s in ["thumbnail", "poster", "placeholder"]):
                    continue
                label = "1080p" if "1080" in u else "720p" if "720" in u else "HD"
                streams.append(
                    {
                        "url": u,
                        "resolution": label,
                        "label": label,
                        "source": "playeriframe",
                    }
                )

            # JS file/src keys
            js_found = re.findall(
                r'(?:file|src|source|url)\s*[=:]\s*["\'](https?://[^"\']+)["\']', html
            )
            for u in dict.fromkeys(js_found):
                u = u.strip()
                if u and u not in {s["url"] for s in streams}:
                    if any(ext in u for ext in [".m3u8", ".mp4", ".ts"]):
                        label = "1080p" if "1080" in u else "HD"
                        streams.append(
                            {
                                "url": u,
                                "resolution": label,
                                "label": label,
                                "source": "playeriframe",
                            }
                        )

            # Nested iframes
            nested = re.findall(r'<iframe[^>]+src=["\'](https?://[^"\']+)["\']', html)
            for ni in nested[:2]:
                ni = ni.strip()
                if ni and ni != url:
                    res = await _resolve_generic(ni, url)
                    streams.extend(res)

            logger.info(f"playeriframe {url}: {len(streams)} streams")
    except Exception as e:
        logger.debug(f"playeriframe error: {e}")
    return streams
