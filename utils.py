import random
from typing import Callable, Awaitable, Optional

import aiohttp
from playwright.async_api import ElementHandle


async def is_url_valid(url: str) -> bool:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123 Safari/537.36",
            "Referer": "https://www.pinterest.com/"
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=5, ssl=False) as resp:
                return resp.status == 200
    except Exception as e:
        print(f"⚠️ Error checking URL: {url} — {e}")
        return False

async def smart_scroll(
        page,
        selector: str,
        keyword: str,
        extract_fn: Callable[[ElementHandle, str], Awaitable[Optional[tuple[str, set[str]]]]],
        db_save_fn: Optional[Callable[[str, list[str], str], None]] = None,
        source: str = "unknown",
        min_images: int = 20,
        max_scrolls: int = 15
) -> list[str]:
    """Scrolls and fetches image URLs using a custom extractor and optional DB saving logic."""
    image_urls = set()
    scroll_attempts = 0


    # Scroll randomly to initial point
    initial_position = random.randint(2000, 10000)
    await page.evaluate(f"window.scrollTo(0, {initial_position})")
    await page.wait_for_timeout(random.randint(1000, 2000))

    while len(image_urls) < min_images and scroll_attempts < max_scrolls:
        images = await page.query_selector_all(selector)

        random.shuffle(images)

        for img in images:

            if len(image_urls) >= min_images:
                break

            result = await extract_fn(img, keyword)
            if result:
                url, query_terms = result

                if url in image_urls:
                    continue

                image_urls.add(url)

                #print(f"✅ New image: {url}")
                print(f"🔍 Keywords: {query_terms}")

                if db_save_fn:
                    db_save_fn(url, list(query_terms), source)

        if len(image_urls) >= min_images:
            break

        scroll_distance = random.randint(3000, 8000)
        scroll_delay = random.randint(2000, 4000)
        scroll_attempts += 1
        await page.mouse.wheel(0, scroll_distance)
        await page.wait_for_timeout(scroll_delay)

    return list(image_urls)
