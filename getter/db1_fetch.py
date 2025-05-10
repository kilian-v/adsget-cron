import random
import re
from typing import Optional

from playwright.async_api import async_playwright
import os
from urllib.parse import quote
from dotenv import load_dotenv

from supabase_client import upsert_image_with_query
from utils import is_url_valid, smart_scroll

load_dotenv()

DB1_BASE_URL = os.getenv("DB1_BASE_URL")
DB1_QUERY_URL = os.getenv("DB1_QUERY_URL")
DB1_NAME=os.getenv("DB1_NAME")


def build_db1_url(keyword: str) -> str:
    return f"{DB1_BASE_URL}{quote(keyword)}"

async def extract_image_data(img, keyword: str) -> Optional[tuple[str, set[str]]]:
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

    if not await is_url_valid(clean_src):
        return None

    query_terms = {keyword.lower()}
    if alt:
        query_terms.add(alt.lower())

    return clean_src, query_terms


async def fetch_db1_urls(keyword: str, limit: int = 20) -> list:

    url = build_db1_url(keyword)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
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
            db_save_fn=upsert_image_with_query,
            source=DB1_NAME,
            max_scrolls=50
        )
        print(len(urls))

        # Deduplicate + shuffle
        unique_urls = list(set(urls))
        random.shuffle(unique_urls)

        print(f"[DB1] Requested: {limit} — Retrieved: {len(urls)}")
        return unique_urls[:limit]


