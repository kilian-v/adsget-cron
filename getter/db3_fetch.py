import os
import random
from typing import Callable, Optional, Tuple, Set
from urllib.parse import quote

from dotenv import load_dotenv
from playwright.async_api import async_playwright, ElementHandle
from urllib.parse import urlparse, urlunparse

from supabase_client import fetch_known_image_urls, upsert_image_with_query
from utils import smart_scroll, is_url_valid

load_dotenv()

DB3_BASE_URL = os.getenv("DB3_BASE_URL")
DB3_QUERY_URL = os.getenv("DB3_QUERY_URL")
DB3_NAME = os.getenv("DB3_NAME")

HEADLESS = os.getenv("HEADLESS", "1") != "0"

MAX_SKIP_PAGES = 10

# Shot thumbnails are lazy-loaded: `src` holds a base64 placeholder and the
# real CDN URL sits in `data-src` (or in `srcset` for the few shots already in
# the viewport). Selecting on `src` — as this fetcher used to — matched only
# avatars and site assets, which is why it returned 0 images.
THUMBNAIL_SELECTOR = "li.shot-thumbnail img"
URL_ATTRIBUTES = ("srcset", "data-srcset", "data-src", "src")


def build_db3_url(keyword: str) -> str:
    return f"{DB3_BASE_URL}{quote(keyword)}"


def strip_query(url: str) -> str:
    """Drop ?crop=…&format=webp&resize=… to get the original upload."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query=""))


async def _best_url(img: ElementHandle) -> Optional[str]:
    for attr in URL_ATTRIBUTES:
        value = await img.get_attribute(attr)
        if not value:
            continue
        if attr.endswith("srcset"):
            # Last candidate of a srcset is the largest one.
            candidates = [c.strip().split(" ")[0] for c in value.split(",") if c.strip()]
            value = candidates[-1] if candidates else None
        if value and DB3_QUERY_URL in value:
            return value
    return None


async def extract_image_data_db3(img: ElementHandle, keyword: str, is_seen: Callable[[str], bool]) -> Optional[Tuple[str, Set[str]]]:
    src = await _best_url(img)
    if not src:
        return None

    cleaned = strip_query(src)

    # Avatars and UI assets live on the same CDN.
    if "/avatars/" in cleaned or "/assets/" in cleaned:
        return None

    if is_seen(cleaned):
        return None

    if not await is_url_valid(cleaned):
        return None

    alt = await img.get_attribute("alt")
    query_terms = {keyword.lower()}
    if alt:
        query_terms.add(alt.lower())

    return cleaned, query_terms


async def fetch_db3_urls(keyword: str, limit: int = 20, seen_urls: Optional[set[str]] = None,
        save: bool = True) -> list:
    url = build_db3_url(keyword)

    if seen_urls is None:
        seen_urls = fetch_known_image_urls(DB3_NAME)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123 Safari/537.36")
        page = await context.new_page()

        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(3000)

        urls = await smart_scroll(
            page=page,
            selector=THUMBNAIL_SELECTOR,
            keyword=keyword,
            extract_fn=extract_image_data_db3,
            db_save_fn=upsert_image_with_query if save else None,
            source=DB3_NAME,
            min_images=limit,
            max_scrolls=30,
            seen_urls=seen_urls,
            skip_pages=random.randint(0, MAX_SKIP_PAGES),
        )

        await context.close()
        await browser.close()

        random.shuffle(urls)

        print(f"[DB3] Requested: {limit} — New images: {len(urls)} — query: {keyword!r}")
        return urls[:limit]
