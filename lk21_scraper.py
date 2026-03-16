"""
CloudMovies — LK21 Scraper (Movies Only)
Search LK21 and extract stream URLs from movie pages.
"""

import asyncio
import logging
import re
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


async def _fetch_lk21_page(url: str) -> dict | None:
    """Fetch a LK21 movie page and extract metadata. Returns None if not a real movie page."""
    try:
        async with _client() as client:
            r = await client.get(url, timeout=10)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")

            # Must have a real movie title (not homepage)
            title_el = soup.select_one("h1.entry-title, h1, .judul-film, .film-title")
            if not title_el:
                title_el = soup.select_one("h2, .title")
            if not title_el:
                return None
            found_title = re.sub(r"\s+", " ", title_el.get_text()).strip()

            # Reject if it looks like homepage or search page
            reject_phrases = [
                "nonton film",
                "layarkaca21",
                "lk21",
                "sub indo gratis",
                "streaming",
            ]
            if any(p in found_title.lower() for p in reject_phrases):
                return None
            if len(found_title) < 2:
                return None

            img_el = soup.select_one(
                ".poster img, .thumb img, .gmr-item-result img, img[class*=poster]"
            )
            if not img_el:
                img_el = soup.select_one("article img, .entry-content img")
            poster = ""
            if img_el:
                poster = img_el.get("src") or img_el.get("data-src", "")
                if poster and poster.startswith("//"):
                    poster = "https:" + poster

            # Extract year from page
            year_el = soup.select_one(
                ".year, .gmr-movie-on, time, [itemprop=dateCreated]"
            )
            page_year = ""
            if year_el:
                page_year = _extract_year(year_el.get_text())
            if not page_year:
                page_year = _extract_year(soup.get_text()[:2000])

            desc_el = soup.select_one(
                ".synopsis p, .description p, .entry-content p, [itemprop=description]"
            )
            desc = (
                re.sub(r"\s+", " ", desc_el.get_text()).strip()[:300] if desc_el else ""
            )

            score_el = soup.select_one(".imdb, .rating, [itemprop=ratingValue]")
            score = _extract_rating(score_el.get_text() if score_el else "")

            logger.info(f"LK21 direct URL hit: {url} → {found_title}")
            return {
                "subjectId": f"lk21_{abs(hash(url)) % 10**10}",
                "title": found_title,
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
        logger.debug(f"LK21 page fetch miss {url}: {e}")
    return None


async def _try_direct_url(title: str, year: str) -> dict | None:
    """
    Try fetching LK21 movie page by slug.
    If year given: try /title-year
    If no year: try /title-year for years 2024..2000 (stop on first hit, max 8 tries)
    """
    base_slug = _make_slug(title, "")  # slug without year

    # If year is known, just try that one
    if year:
        url = f"{LK21_URL}/{base_slug}-{year}"
        result = await _fetch_lk21_page(url)
        if result:
            return result
        # Also try without year suffix
        url2 = f"{LK21_URL}/{base_slug}"
        return await _fetch_lk21_page(url2)

    # No year — try years from recent to old (max 8 attempts)
    import datetime

    current_year = datetime.datetime.now().year
    # Go back to 1990 to cover all classic movies
    years_to_try = list(range(current_year, 1989, -1))

    # Try in batches of 8 concurrently to avoid overwhelming LK21
    found = None
    for i in range(0, len(years_to_try), 8):
        batch = years_to_try[i : i + 8]
        tasks = [_fetch_lk21_page(f"{LK21_URL}/{base_slug}-{y}") for y in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if r and not isinstance(r, Exception):
                return r
    return await _fetch_lk21_page(f"{LK21_URL}/{base_slug}")

    # (unreachable - kept for structure)
    tasks = [_fetch_lk21_page(f"{LK21_URL}/{base_slug}-{y}") for y in years_to_try]
    return None  # already handled above


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
                    poster = (
                        img_el.get("src")
                        or img_el.get("data-src")
                        or img_el.get("data-lazy-src")
                        or ""
                    )
                    if poster.startswith("//"):
                        poster = "https:" + poster

                year = _extract_year(card.get_text())
                score_el = card.select_one(".rating, .imdb, .score")
                score = _extract_rating(score_el.get_text() if score_el else "")
                genre_els = card.select(".genre a, .categories a, .cat a")
                genres = ", ".join(g.get_text(strip=True) for g in genre_els[:3])

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
            if (
                src
                and src.startswith("http")
                and not any(d in src for d in skip_domains)
            ):
                embed_urls.append(src)

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
