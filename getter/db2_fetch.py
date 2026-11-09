import os
import random
from typing import Callable, Optional, Set, Tuple

from dotenv import load_dotenv
from playwright.async_api import async_playwright, ElementHandle
from urllib.parse import quote

from supabase_client import fetch_known_image_urls, upsert_image_with_query
from utils import smart_scroll, is_url_valid

load_dotenv()

DB2_BASE_URL = os.getenv("DB2_BASE_URL")
DB2_QUERY_URL = os.getenv("DB2_QUERY_URL")
DB2_NAME = os.getenv("DB2_NAME")

HEADLESS = os.getenv("HEADLESS", "1") != "0"

MAX_SKIP_PAGES = 10


def build_db2_url(keyword: str) -> str:
    return f"{DB2_BASE_URL}{quote(keyword)}?field=advertising"

async def extract_image_data_db2(img: ElementHandle, keyword: str, is_seen: Callable[[str], bool]) -> Optional[Tuple[str, Set[str]]]:
    alt = await img.get_attribute("alt")
    src = await img.get_attribute("src")

    if not src or "/disp/" not in src:
        return None

    clean_src = src.replace("/disp/", "/source/")

    if is_seen(clean_src):
        return None

    if not await is_url_valid(clean_src):
        return None

    query_terms = {keyword.lower()}
    if alt:
        query_terms.add(alt.lower())

    return clean_src, query_terms

async def fetch_db2_image_urls(keyword: str, limit: int = 20, seen_urls: Optional[set[str]] = None,
        save: bool = True) -> list:
    url = build_db2_url(keyword)

    if seen_urls is None:
        seen_urls = fetch_known_image_urls(DB2_NAME)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123 Safari/537.36")
        page = await context.new_page()

        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(3000)

        urls = await smart_scroll(
            page=page,
            selector=f"img[src*='{DB2_QUERY_URL}']",
            keyword=keyword,
            extract_fn=extract_image_data_db2,
            db_save_fn=upsert_image_with_query if save else None,
            source=DB2_NAME,
            min_images=limit,
            max_scrolls=30,
            seen_urls=seen_urls,
            skip_pages=random.randint(0, MAX_SKIP_PAGES),
        )

        await context.close()
        await browser.close()

        random.shuffle(urls)

        print(f"[DB2] Requested: {limit} — New images: {len(urls)} — query: {keyword!r}")
        return urls[:limit]
