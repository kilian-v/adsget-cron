"""Query generation and rotation.

The scraper used to issue one fixed query per category (e.g. "food & beverage
ads"), which always landed on the same deterministic top-of-feed results. This
module expands each category into a large combinatorial query space and rotates
through it, preferring queries that have never been used, then the
least-recently-used ones.
"""

import random
from datetime import datetime, timezone
from typing import Optional

from supabase_client import supabase

# Base search terms per category. Keep them phrased the way a human would
# search on an image board, not the way the category is labelled internally.
CATEGORY_SEEDS: dict[str, list[str]] = {
    "all": [
        "ads", "advertising", "ad campaign", "advertisement design",
        "commercial poster", "brand campaign", "marketing visual",
        "creative advertising",
    ],
    "electronics": [
        "tech ads", "gadget advertising", "electronics campaign",
        "consumer tech ad", "audio product ad", "smartphone advertising",
    ],
    "apparel": [
        "fashion ads", "clothing campaign", "streetwear advertising",
        "apparel lookbook", "sneaker ad", "fashion editorial ad",
    ],
    "home & kitchen": [
        "home decor ads", "kitchenware advertising", "furniture campaign",
        "homeware ad", "interior product ad",
    ],
    "beauty & personal care": [
        "beauty ads", "skincare advertising", "cosmetics campaign",
        "perfume ad", "haircare ad", "makeup product ad",
    ],
    "sports & outdoors": [
        "sports ads", "athletic campaign", "outdoor gear advertising",
        "running ad", "fitness campaign", "sportswear ad",
    ],
    "toys & games": [
        "toy ads", "board game advertising", "kids product campaign",
        "video game ad", "playful product ad",
    ],
    "books & media": [
        "book cover ads", "publishing campaign", "magazine advertising",
        "film poster", "music album advertising",
    ],
    "food & beverage": [
        "food ads", "beverage advertising", "drink campaign",
        "restaurant ad", "coffee advertising", "snack packaging ad",
        "juice campaign", "craft beer ad",
    ],
    "health & wellness": [
        "wellness ads", "supplement advertising", "fitness campaign",
        "pharmacy ad", "mental health campaign", "healthcare advertising",
        "vitamins packaging ad",
    ],
    "jewelry & accessories": [
        "jewelry ads", "watch advertising", "accessories campaign",
        "luxury jewelry ad", "eyewear advertising", "handbag campaign",
    ],
}

# Output format / medium.
FORMATS: list[str] = [
    "poster", "flyer", "packaging", "billboard", "social media ad",
    "banner", "print ad", "magazine spread", "label design",
    "product photography", "branding", "outdoor campaign",
]

# Visual direction.
STYLES: list[str] = [
    "minimal", "retro", "vintage", "bold typography", "gradient",
    "grainy", "collage", "90s", "y2k", "editorial", "pastel",
    "monochrome", "luxury", "playful", "brutalist", "art deco",
    "swiss design", "risograph", "neon", "surreal",
]

TABLE = "search_queries"


def _normalize(category: str) -> str:
    return category.strip().lower()


def build_query_space(category: str) -> list[str]:
    """All candidate queries for a category.

    Mixes two-part and three-part queries: three-part ones are more specific
    (deeper into the long tail, more novel results) but can return too little,
    so the pool keeps the broader combinations as well.
    """
    key = _normalize(category)
    seeds = CATEGORY_SEEDS.get(key)
    if not seeds:
        # Unknown category: fall back to a sane base term built from its name.
        seeds = [f"{key} ads", f"{key} advertising"]

    combos: set[str] = set()
    for seed in seeds:
        combos.add(seed)
        for fmt in FORMATS:
            combos.add(f"{seed} {fmt}")
        for style in STYLES:
            combos.add(f"{seed} {style}")
        for fmt in FORMATS:
            for style in STYLES:
                combos.add(f"{style} {seed} {fmt}")
    return sorted(combos)


def _fetch_usage(category: str) -> dict[str, Optional[str]]:
    """query -> last_used_at, for queries already used in this category.

    Returns an empty dict if the table is missing or unreachable, so query
    rotation degrades to plain random selection instead of failing the run.
    """
    usage: dict[str, Optional[str]] = {}
    page_size = 1000
    offset = 0
    try:
        while True:
            res = (
                supabase.table(TABLE)
                .select("query,last_used_at")
                .eq("category", category)
                .range(offset, offset + page_size - 1)
                .execute()
            )
            rows = res.data or []
            for row in rows:
                usage[row["query"]] = row.get("last_used_at")
            if len(rows) < page_size:
                break
            offset += page_size
    except Exception as e:
        print(f"[queries] usage lookup failed ({e}) — falling back to random choice")
        return {}
    return usage


def pick_query(category: str) -> str:
    """Pick the next query for a category: unused first, then least recent."""
    space = build_query_space(category)
    usage = _fetch_usage(category)

    unused = [q for q in space if q not in usage]
    if unused:
        return random.choice(unused)

    # Everything has been used at least once: take the oldest, with a little
    # jitter so parallel runs don't collide on the exact same query.
    known = [q for q in space if q in usage]
    if not known:
        return random.choice(space)
    known.sort(key=lambda q: usage.get(q) or "")
    window = known[: max(1, len(known) // 20)]
    return random.choice(window)


def record_query_use(category: str, query: str, new_images: int) -> None:
    """Mark a query as used. Best-effort: never fails the scraping run."""
    try:
        supabase.table(TABLE).upsert(
            {
                "category": category,
                "query": query,
                "last_used_at": datetime.now(timezone.utc).isoformat(),
                "last_new_images": new_images,
            },
            on_conflict="category,query",
        ).execute()
    except Exception as e:
        print(f"[queries] could not record use of {query!r}: {e}")
