"""Ads of the World fetcher — no browser.

Unlike pinterest/behance/dribbble, this site serves plain HTML, so this fetcher
uses aiohttp instead of Playwright. That makes it roughly two orders of
magnitude cheaper per image.

Two constraints shape the design:

* There is no keyword search. `?q=` is ignored (byte-identical HTML), and
  /search 404s. Relevance therefore comes from the site's own axes:
  /industries/<slug> and /medium_types/<slug>, which DO filter (only ~18 of 120
  images overlap between /industries/food and /medium_types/print).
* robots.txt forbids `/*?page=`, so listing pages cannot be paginated. Depth
  comes from sitemap*.xml.gz instead, which exposes ~82k campaign URLs and is
  explicitly advertised in robots.txt.

Listing pages only carry small thumbnails; the full-size creatives live on the
campaign pages, as <img width="4000" …>. So listings are used to DISCOVER
campaign URLs, and the campaign pages to extract images.
"""

import asyncio
import gzip
import html as html_lib
import os
import random
import re
import ssl
from typing import Optional

import aiohttp
import certifi
from dotenv import load_dotenv

from supabase_client import fetch_known_image_urls, upsert_image_with_query

load_dotenv()

DB4_BASE_URL = (os.getenv("DB4_BASE_URL") or "https://www.adsoftheworld.com").rstrip("/")
DB4_NAME = os.getenv("DB4_NAME") or "adsoftheworld"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123 Safari/537.36"
)

# Be polite: the site has no Crawl-Delay, but this is still a crawl.
CONCURRENCY = 4
DELAY_RANGE = (0.3, 0.9)

# Only the hero creatives of a campaign are this wide; related-campaign
# thumbnails in the sidebar declare width="544" or less.
MIN_IMAGE_WIDTH = 1000

# A campaign holds 3-10 variations of the same creative. Taking them all makes
# a run look full while covering very few actual ads.
MAX_IMAGES_PER_CAMPAIGN = 2

IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
WIDTH_RE = re.compile(r"\bwidth=[\"'](\d+)[\"']", re.I)
SRC_RE = re.compile(r"\bsrc=[\"'](https://image\.adsoftheworld\.com/[^\"']+)[\"']", re.I)
CAMPAIGN_RE = re.compile(r"/campaigns/([a-z0-9][a-z0-9-]*)", re.I)
TITLE_RE = re.compile(r"<title>([^<]*)</title>", re.I)
INDUSTRY_RE = re.compile(r"/industries/([a-z-]+)")
LOC_RE = re.compile(r"<loc>([^<]+)</loc>")

# Query text -> the site's own industry slugs. The generated queries always
# contain one of these seed words (see queries.CATEGORY_SEEDS).
INDUSTRY_HINTS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("food", "snack", "restaurant", "coffee"), ("food", "confectionery-snacks")),
    (("beverage", "drink", "juice", "beer"), ("non-alcoholic-drinks", "alcoholic-drinks")),
    (("health", "wellness", "supplement", "pharmacy", "vitamin", "healthcare"),
     ("health", "pharmaceutical")),
    (("fitness", "sport", "running", "athletic"), ("fitness", "sports", "sportswear")),
    (("jewelry", "watch", "eyewear", "handbag", "accessor"),
     ("jewelry", "personal-accessories")),
    (("beauty", "skincare", "cosmetic", "perfume", "haircare", "makeup"), ("beauty",)),
    (("fashion", "clothing", "apparel", "streetwear", "sneaker"), ("fashion",)),
    (("tech", "gadget", "electronic", "smartphone", "audio"),
     ("electronics-technology", "electronic-devices")),
    (("home", "kitchen", "furniture", "interior", "homeware"),
     ("house-garden", "household-products")),
    (("toy", "game", "kids"), ("toys", "gaming")),
    (("book", "magazine", "film", "music", "media", "publishing"), ("media", "movies", "music")),
    (("pet",), ("pets",)),
    (("travel", "tourism", "hotel"), ("travel", "hospitality-tourism")),
]

# Query text -> medium types. These are the poster-shaped ones.
MEDIUM_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("poster", "print", "magazine", "flyer", "label"), "print"),
    (("billboard", "outdoor"), "outdoor"),
    (("packaging", "branding", "design"), "design"),
    (("social media", "banner", "digital"), "digital"),
]

DEFAULT_MEDIUMS = ("print", "outdoor", "design")


def hinted_industries(keyword: str) -> list[str]:
    """The site's industry slugs implied by this query, if any."""
    text = keyword.lower()
    industries: list[str] = []
    for needles, slugs in INDUSTRY_HINTS:
        if any(n in text for n in needles):
            industries.extend(slugs)
    return list(dict.fromkeys(industries))


def listing_paths(keyword: str) -> list[str]:
    """Listing pages worth reading for this query, most specific first."""
    text = keyword.lower()
    industries = hinted_industries(keyword)

    mediums = [slug for needles, slug in MEDIUM_HINTS if any(n in text for n in needles)]
    if not mediums:
        mediums = list(DEFAULT_MEDIUMS)

    # Industry listings first: they are the only ones that guarantee the
    # campaign matches the query's subject. Medium listings come after, as a
    # top-up, and /professional last as a catch-all.
    industry_paths = [f"/industries/{s}" for s in industries]
    medium_paths = [f"/medium_types/{s}" for s in dict.fromkeys(mediums)]
    random.shuffle(industry_paths)
    random.shuffle(medium_paths)
    return industry_paths + medium_paths


async def _get(session: aiohttp.ClientSession, url: str) -> Optional[bytes]:
    try:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                print(f"⚠️ [DB4] {resp.status} on {url}")
                return None
            return await resp.read()
    except Exception as e:
        print(f"⚠️ [DB4] failed {url}: {e}")
        return None


def parse_campaign_slugs(html: str) -> list[str]:
    return list(dict.fromkeys(CAMPAIGN_RE.findall(html)))


def _unescape_fully(text: str, limit: int = 3) -> str:
    """Decode HTML entities, repeatedly.

    Ads of the World escapes its titles twice: the page for "Hertz & Health"
    serves `Hertz &amp;amp; Health`, so a single unescape leaves `&amp;`
    behind. These strings become search keys in image_queries, where a stray
    entity means the campaign can never be matched by a user's query.
    """
    for _ in range(limit):
        decoded = html_lib.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


def parse_campaign_images(html: str) -> tuple[list[str], set[str], set[str]]:
    """Full-size creatives of a campaign page, plus its query terms."""
    images: list[str] = []
    for tag in IMG_TAG_RE.findall(html):
        src = SRC_RE.search(tag)
        if not src:
            continue
        url = src.group(1)
        if "/static/" in url:
            continue
        width = WIDTH_RE.search(tag)
        if not width or int(width.group(1)) < MIN_IMAGE_WIDTH:
            continue
        images.append(url)

    terms: set[str] = set()
    title = TITLE_RE.search(html)
    if title:
        # "Pronaca: A Taste of Art • Ads of the World™ | Part of The Clio Network"
        clean = _unescape_fully(title.group(1)).split("•")[0].strip().lower()
        if clean:
            terms.add(clean)
            if ":" in clean:
                brand, campaign = clean.split(":", 1)
                terms.update({brand.strip(), campaign.strip()})
    industries = set(INDUSTRY_RE.findall(html)[:3])
    terms.update(industries)
    return list(dict.fromkeys(images)), {t for t in terms if t}, industries


async def _sitemap_campaign_slugs(session: aiohttp.ClientSession, max_sitemaps: int = 3) -> list[str]:
    """Campaign slugs from a few randomly chosen sub-sitemaps (~82k in total)."""
    index = await _get(session, f"{DB4_BASE_URL}/sitemap.xml.gz")
    if not index:
        return []
    try:
        index_xml = gzip.decompress(index).decode("utf-8", "replace")
    except Exception as e:
        print(f"⚠️ [DB4] cannot read sitemap index: {e}")
        return []

    sub = LOC_RE.findall(index_xml)
    random.shuffle(sub)
    slugs: list[str] = []
    for url in sub[:max_sitemaps]:
        raw = await _get(session, url)
        if not raw:
            continue
        try:
            xml = gzip.decompress(raw).decode("utf-8", "replace")
        except Exception:
            continue
        slugs.extend(
            loc.rsplit("/campaigns/", 1)[1]
            for loc in LOC_RE.findall(xml)
            if "/campaigns/" in loc
        )
        if slugs:
            break
    random.shuffle(slugs)
    return slugs


async def fetch_db4_urls(keyword: str, limit: int = 20, seen_urls: Optional[set[str]] = None,
        save: bool = True) -> list:
    if seen_urls is None:
        seen_urls = fetch_known_image_urls(DB4_NAME)

    collected: list[str] = []
    visited_slugs: set[str] = set()
    wanted_industries = hinted_industries(keyword)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en"}
    # Verify TLS against certifi's bundle: a bare venv on macOS has no CA store
    # of its own, which makes aiohttp fail with CERTIFICATE_VERIFY_FAILED.
    connector = aiohttp.TCPConnector(
        ssl=ssl.create_default_context(cafile=certifi.where()),
        limit=CONCURRENCY,
    )

    async with aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector) as session:

        async def campaign_images(slug: str, medium_slug: Optional[str] = None) -> None:
            if len(collected) >= limit or slug in visited_slugs:
                return
            visited_slugs.add(slug)
            async with semaphore:
                await asyncio.sleep(random.uniform(*DELAY_RANGE))
                raw = await _get(session, f"{DB4_BASE_URL}/campaigns/{slug}")
            if not raw:
                return
            images, terms, industries = parse_campaign_images(raw.decode("utf-8", "replace"))

            # The generated query mixes a subject ("coffee advertising") with a
            # visual style ("gradient", "risograph"). This site can filter on
            # industry and medium, never on style, so attaching the whole query
            # would label a Chevrolet ad as "gradient ad campaign flyer".
            # The query is kept only when the site itself confirms the subject,
            # i.e. the campaign really is in one of the hinted industries.
            subject_confirmed = bool(wanted_industries) and bool(industries & set(wanted_industries))
            query_terms = set(terms)
            if medium_slug:
                query_terms.add(medium_slug)
            if subject_confirmed:
                query_terms.add(keyword.lower())
            taken = 0
            for url in images:
                if len(collected) >= limit or taken >= MAX_IMAGES_PER_CAMPAIGN:
                    return
                if url in seen_urls:
                    continue
                taken += 1
                seen_urls.add(url)
                collected.append(url)
                print(f"🔍 Keywords: {query_terms}")
                if save:
                    try:
                        upsert_image_with_query(url, list(query_terms), DB4_NAME)
                    except Exception as e:
                        print(f"⚠️ save failed for {url}: {e}")

        # 1. Relevance: the site's own industry / medium listings.
        for path in listing_paths(keyword):
            if len(collected) >= limit:
                break
            raw = await _get(session, f"{DB4_BASE_URL}{path}")
            if not raw:
                continue
            slugs = parse_campaign_slugs(raw.decode("utf-8", "replace"))
            # /medium_types/print proves the format, not the subject.
            medium = path.rsplit("/", 1)[1] if path.startswith("/medium_types/") else None
            print(f"📄 [DB4] {path}: {len(slugs)} campaigns")
            random.shuffle(slugs)
            # With at most MAX_IMAGES_PER_CAMPAIGN per campaign, reaching the
            # limit takes several campaigns — visit enough of them.
            batch = max(12, limit)
            await asyncio.gather(*(campaign_images(s, medium) for s in slugs[:batch]))

        # 2. Depth: random campaigns from the sitemap, once the listings run dry.
        if len(collected) < limit:
            print(f"🕳️ [DB4] only {len(collected)}/{limit} from listings — going to the sitemap")
            deep = await _sitemap_campaign_slugs(session)
            print(f"🗺️ [DB4] {len(deep)} campaign slugs from sitemap")
            for i in range(0, min(len(deep), 300), 12):
                if len(collected) >= limit:
                    break
                await asyncio.gather(*(campaign_images(s) for s in deep[i:i + 12]))

    random.shuffle(collected)
    print(f"[DB4] Requested: {limit} — New images: {len(collected)} — "
          f"campaigns visited: {len(visited_slugs)} — query: {keyword!r}")
    return collected[:limit]
