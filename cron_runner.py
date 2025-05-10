import os, asyncio, random, math
from datetime import datetime
from supabase_client import supabase
from getter.db1_fetch import fetch_db1_urls
from getter.db2_fetch import fetch_db2_image_urls
from getter.db3_fetch import fetch_db3_urls

CATEGORIES  = [
    "All",
    "Electronics",
    "Apparel",
    "Home & Kitchen",
    "Beauty & Personal Care",
    "Sports & Outdoors",
    "Toys & Games",
    "Books & Media",
    "Food & Beverage",
    "Health & Wellness",
    "Jewelry & Accessories",
]
LIMIT = 100

def get_interval_range(index: int) -> tuple[int, int]:
    block_size = math.ceil(24 / len(CATEGORIES))
    start = index * block_size
    end = min(24, (index + 1) * block_size)
    return start, end

def get_current_category(now: datetime) -> tuple[str, str, int]:
    hour = now.hour
    block_size = math.ceil(24 / len(CATEGORIES))
    index = min(hour // block_size, len(CATEGORIES) - 1)
    category = CATEGORIES[index]
    date_key = f"{now.date()}-{category}"
    return category, date_key, index

async def already_ran(date_key: str) -> bool:
    res = supabase.from_("cron_runs").select("*").eq("date_key", date_key).maybe_single().execute()
    return bool(res.data)

async def record_run(date_key: str, category: str):
    supabase.from_("cron_runs").insert({"date_key": date_key, "category": category}).execute()

async def get_or_create_schedule(category: str, date_key: str, interval_index: int) -> tuple[int, int]:
    res = supabase.from_("cron_schedule").select("*").eq("date_key", date_key).maybe_single().execute()
    if res.data:
        return res.data["run_hour"], res.data["run_minute"]

    start_hour, end_hour = get_interval_range(interval_index)
    run_hour = random.randint(start_hour, end_hour - 1)
    run_minute = random.randint(0, 59)

    supabase.from_("cron_schedule").insert({
        "date_key": date_key,
        "category": category,
        "run_hour": run_hour,
        "run_minute": run_minute
    }).execute()

    return run_hour, run_minute

async def fetch_all_for_category(category: str):
    async def safe_fetch(fn, name):
        try:
            query = "best ads" if category.lower() == "all" else f"{category.lower()} ads"
            return await asyncio.wait_for(fn(query, LIMIT), timeout=3600)
        except Exception as e:
            print(f"[ERROR] {name} failed:", e)
            return []

    tasks = [
        safe_fetch(fetch_db1_urls, "DB1"),
        safe_fetch(fetch_db2_image_urls, "DB2"),
    ]

    results = await asyncio.gather(*tasks)
    total = sum(len(r) for r in results)
    print(f"[{category}] Total images collected: {total}")

async def run_cron():
    now = datetime.now()
    category, date_key, interval_index = get_current_category(now)

    if await already_ran(date_key):
        print(f"⏩ Already ran for {category} today.")
        return

    run_hour, run_minute = await get_or_create_schedule(category, date_key, interval_index)

    if now.hour != run_hour or now.minute != run_minute:
        print(f"⏳ Waiting... now={now.hour}:{now.minute} vs target={run_hour}:{run_minute}")
        return

    print(f"🚀 Running scraper for {category} at scheduled time")
    await fetch_all_for_category(category)
    await record_run(date_key, category)



