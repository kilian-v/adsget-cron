import os
import random
from typing import Optional, Tuple, Set
from urllib.parse import quote

from dotenv import load_dotenv
from playwright.async_api import async_playwright, ElementHandle
from urllib.parse import urlparse, urlunparse
from utils import smart_scroll, is_url_valid

load_dotenv()

DB3_BASE_URL = os.getenv("DB3_BASE_URL")
DB3_QUERY_URL = os.getenv("DB3_QUERY_URL")
DB3_NAME=os.getenv("DB3_NAME")

def build_db3_url(keyword: str) -> str:
    return f"{DB3_BASE_URL}{quote(keyword)}"


def strip_query(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query=""))

async def extract_image_data_db3(img: ElementHandle, keyword: str) -> Optional[Tuple[str, Set[str]]]:
    src = await img.get_attribute("src")
    alt = await img.get_attribute("alt")
    if not src:
        return None

    cleaned = strip_query(src)

    if "/file/original-" not in cleaned:
        return None

    if not await is_url_valid(cleaned):
        return None

    query_terms = {keyword.lower()}
    if alt:
        query_terms.add(alt.lower())

    return cleaned, query_terms

async def fetch_db3_urls(keyword: str, limit: int = 20) -> list:
    url = build_db3_url(keyword)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123 Safari/537.36")
        page = await context.new_page()
        #await stealth_async(page)

        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(3000)

        urls = await smart_scroll(
            page=page,
            selector=f"img[src*='{DB3_QUERY_URL}']",
            keyword=keyword,
            extract_fn=extract_image_data_db3,
            #db_save_fn=upsert_image_with_query,
            #source=DB3_NAME,
            min_images=limit,
            max_scrolls=30
        )

        random.shuffle(urls)

        print(f"[DB3] Requested: {limit} — Retrieved: {len(urls)}")
        return urls[:limit]
