import random
import re
from typing import Callable, Optional

from playwright.async_api import async_playwright
import os
from urllib.parse import quote
from dotenv import load_dotenv

from supabase_client import fetch_known_image_urls, upsert_image_with_query
from utils import is_url_valid, smart_scroll

load_dotenv()

DB1_BASE_URL = os.getenv("DB1_BASE_URL")
DB1_QUERY_URL = os.getenv("DB1_QUERY_URL")
DB1_NAME = os.getenv("DB1_NAME")

HEADLESS = os.getenv("HEADLESS", "1") != "0"

MAX_SKIP_PAGES = 12


def build_db1_url(keyword: str) -> str:
    return f"{DB1_BASE_URL}{quote(keyword)}"

async def extract_image_data(img, keyword: str, is_seen: Callable[[str], bool]) -> Optional[tuple[str, set[str]]]:
    alt = await img.get_attribute("alt")
    srcset = await img.get_attribute("srcset")
    src_url = None

    if srcset:
        candidates = [part.strip().split(" ")[0] for part in srcset.split(",") if part.strip()]
        if candidates:
            src_url = candidates[-1]
    else:
        src_url = await img.get_attribute("src")

    if not src_url or "thumbnails" in src_url:
        return None

    clean_src = re.sub(r"(https://i\.pinimg\.com/)\d+x", r"\1originals", src_url)

    # Skip before validating: an already-known image is not worth a request.
    if is_seen(clean_src):
        return None

    if not await is_url_valid(clean_src):
        return None

    query_terms = {keyword.lower()}
    if alt:
        query_terms.add(alt.lower())

    return clean_src, query_terms


async def fetch_db1_urls(keyword: str, limit: int = 20, seen_urls: Optional[set[str]] = None,
        save: bool = True) -> list:

    url = build_db1_url(keyword)

    if seen_urls is None:
        seen_urls = fetch_known_image_urls(DB1_NAME)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123 Safari/537.36")
        page = await context.new_page()

        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(3000)

        urls = await smart_scroll(
            page=page,
            selector=f"img[src^='{DB1_QUERY_URL}']",
            keyword=keyword,
            extract_fn=extract_image_data,
            min_images=limit,
            db_save_fn=upsert_image_with_query if save else None,
            source=DB1_NAME,
            max_scrolls=50,
            seen_urls=seen_urls,
            skip_pages=random.randint(0, MAX_SKIP_PAGES),
        )

        await context.close()
        await browser.close()

        random.shuffle(urls)

        print(f"[DB1] Requested: {limit} — New images: {len(urls)} — query: {keyword!r}")
        return urls[:limit]
