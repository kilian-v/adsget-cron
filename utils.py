import asyncio
import random
from typing import Awaitable, Callable, Optional

import aiohttp
from playwright.async_api import ElementHandle

# URL validation used to fire an unbounded number of full GETs. HEAD is enough
# to know the asset resolves, and the semaphore keeps the source from seeing a
# burst of parallel requests.
_VALIDATE_SEMAPHORE = asyncio.Semaphore(10)

_VALIDATE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123 Safari/537.36",
    "Referer": "https://www.pinterest.com/",
}


async def is_url_valid(url: str) -> bool:
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with _VALIDATE_SEMAPHORE:
            async with aiohttp.ClientSession(headers=_VALIDATE_HEADERS, timeout=timeout) as session:
                async with session.head(url, ssl=False, allow_redirects=True) as resp:
                    if resp.status == 200:
                        return True
                    # Some CDNs refuse HEAD; fall back to a ranged GET.
                    if resp.status in (403, 405, 501):
                        async with session.get(
                            url, ssl=False, headers={"Range": "bytes=0-0"}
                        ) as get_resp:
                            return get_resp.status in (200, 206)
                    return False
    except Exception as e:
        print(f"⚠️ Error checking URL: {url} — {e}")
        return False


async def smart_scroll(
        page,
        selector: str,
        keyword: str,
        extract_fn: Callable[[ElementHandle, str, Callable[[str], bool]], Awaitable[Optional[tuple[str, set[str]]]]],
        db_save_fn: Optional[Callable[[str, list[str], str], None]] = None,
        source: str = "unknown",
        min_images: int = 20,
        max_scrolls: int = 15,
        seen_urls: Optional[set[str]] = None,
        skip_pages: int = 0,
) -> list[str]:
    """Scroll a feed and collect image URLs that are not already known.

    `seen_urls` is the set of URLs already stored for this source. Images in it
    are skipped before any network validation, and `min_images` counts only
    genuinely new URLs — so the run keeps scrolling instead of stopping on the
    same top-of-feed results it collected last time.

    `skip_pages` scrolls past the head of the feed before collecting anything,
    which is what actually reaches new material on a deterministic ranking.
    """
    seen: set[str] = seen_urls if seen_urls is not None else set()
    collected: list[str] = []
    scroll_attempts = 0

    def is_seen(url: str) -> bool:
        return url in seen

    # Scroll randomly to initial point
    initial_position = random.randint(2000, 10000)
    await page.evaluate(f"window.scrollTo(0, {initial_position})")
    await page.wait_for_timeout(random.randint(1000, 2000))

    # Jump deeper into the feed before collecting.
    for _ in range(skip_pages):
        await page.mouse.wheel(0, random.randint(4000, 9000))
        await page.wait_for_timeout(random.randint(1200, 2500))
    if skip_pages:
        print(f"⏭️ Skipped {skip_pages} page(s) into the feed before collecting")

    while len(collected) < min_images and scroll_attempts < max_scrolls:
        images = await page.query_selector_all(selector)

        random.shuffle(images)

        for img in images:

            if len(collected) >= min_images:
                break

            try:
                result = await extract_fn(img, keyword, is_seen)
            except Exception as e:
                print(f"⚠️ extract failed: {e}")
                continue

            if not result:
                continue

            url, query_terms = result

            if url in seen:
                continue

            seen.add(url)
            collected.append(url)

            print(f"🔍 Keywords: {query_terms}")

            if db_save_fn:
                try:
                    db_save_fn(url, list(query_terms), source)
                except Exception as e:
                    print(f"⚠️ save failed for {url}: {e}")

        if len(collected) >= min_images:
            break

        scroll_distance = random.randint(3000, 8000)
        scroll_delay = random.randint(2000, 4000)
        scroll_attempts += 1
        await page.mouse.wheel(0, scroll_distance)
        await page.wait_for_timeout(scroll_delay)

    print(f"📦 Collected {len(collected)} new image(s) after {scroll_attempts} scroll(s)")
    return collected
