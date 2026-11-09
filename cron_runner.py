import os, asyncio, random, math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from supabase_client import supabase, fetch_known_image_urls
from queries import pick_query, record_query_use
from getter.db1_fetch import fetch_db1_urls
from getter.db2_fetch import fetch_db2_image_urls
from getter.db3_fetch import fetch_db3_urls
from getter.db4_fetch import fetch_db4_urls

TZ = ZoneInfo("Africa/Porto-Novo")

#Max 7
CATEGORIES  = [
    "All",
    #"Electronics",
    #"Apparel",
    #"Home & Kitchen",
    #"Beauty & Personal Care",
    #"Sports & Outdoors",
    #"Toys & Games",
    #"Books & Media",
    "Food & Beverage",
    "Health & Wellness",
    "Jewelry & Accessories",
]
# Per-source budget FOR ONE QUERY. A single query yields roughly 10-15 new
# images before a Playwright source hits max_scrolls and spends minutes for
# scraps, so asking one query for 50 is wasted wall-clock. Ads of the World is
# cheap (plain HTTP, ~8s) and gets a bigger share.
PER_SOURCE_LIMIT = {
    "DB1": 15,
    "DB2": 15,
    "DB3": 15,
    "DB4": 20,
}

# Volume now comes from several queries per run instead of one greedy query.
TARGET_PER_RUN = 60
MAX_QUERIES_PER_RUN = 4

# Kept for callers that still reference it.
LIMIT = max(PER_SOURCE_LIMIT.values())

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

def _maybe_single(table: str, date_key: str):
    """maybe_single() returns a truthy response object even with no rows, and
    raises in some supabase-py versions. Return the row dict, or None."""
    try:
        res = supabase.from_(table).select("*").eq("date_key", date_key).maybe_single().execute()
    except Exception as e:
        print(f"⚠️ {table} lookup failed for {date_key}: {e}")
        return None
    if res and getattr(res, "data", None):
        return res.data
    return None

async def already_ran(date_key: str) -> bool:
    return _maybe_single("cron_runs", date_key) is not None

async def record_run(date_key: str, category: str):
    supabase.from_("cron_runs").insert({"date_key": date_key, "category": category}).execute()

async def get_or_create_schedule(category: str, date_key: str, interval_index: int) -> tuple[int, int]:
    # Check if already exists
    row = _maybe_single("cron_schedule", date_key)
    if row:
        return row["run_hour"], row["run_minute"]

    now = datetime.now(TZ)
    current_hour = now.hour
    current_minute = now.minute

    # Get the valid hour interval for this category
    start_hour, end_hour = get_interval_range(interval_index)

    # ⛔️ If we're too late for this interval, skip
    if current_hour >= end_hour:
        print(f"⛔️ Now ({current_hour}) is past interval end ({end_hour}) for {category}")
        return current_hour, current_minute  # Or raise, or skip

    # Determine first valid hour to consider
    effective_start_hour = max(current_hour, start_hour)

    # 🛑 Final edge case: last possible minute already passed
    if effective_start_hour == end_hour - 1 and current_minute >= 59:
        print(f"⛔️ No more valid minute in last hour ({effective_start_hour}) for {category}")
        return current_hour, current_minute  # Or skip

    # ✅ Choose valid hour
    run_hour = random.randint(effective_start_hour, end_hour - 1)

    # ✅ Choose valid minute
    if run_hour == current_hour:
        if current_minute < 59:
            run_minute = random.randint(current_minute + 1, 59)
        else:
            run_minute = 59  # fallback safe
    else:
        run_minute = random.randint(0, 59)

    print(f"📌 Scheduled {category} at {str(run_hour).zfill(2)}:{str(run_minute).zfill(2)}")

    # Save to Supabase
    supabase.from_("cron_schedule").insert({
        "date_key": date_key,
        "category": category,
        "run_hour": run_hour,
        "run_minute": run_minute
    }).execute()

    return run_hour, run_minute

SOURCES = (
    ("DB1", "DB1_NAME", fetch_db1_urls),
    ("DB2", "DB2_NAME", fetch_db2_image_urls),
    ("DB3", "DB3_NAME", fetch_db3_urls),
    ("DB4", "DB4_NAME", fetch_db4_urls),
)


async def fetch_all_for_category(category: str, save: bool = True) -> int:
    """Collect new images for a category, across several rotating queries.

    The seen-URL set of each source is loaded ONCE and then shared by every
    query of the run: it both saves a full table scan per query and lets a
    later query skip what an earlier one just collected.
    """
    seen: dict[str, set[str]] = {}
    for name, env_name, _ in SOURCES:
        seen[name] = fetch_known_image_urls(os.getenv(env_name))

    total = 0

    for attempt in range(MAX_QUERIES_PER_RUN):
        if total >= TARGET_PER_RUN:
            break

        query = pick_query(category)
        print(f"🔎 [{category}] query {attempt + 1}/{MAX_QUERIES_PER_RUN}: {query!r}")

        async def safe_fetch(fn, name):
            try:
                return await asyncio.wait_for(
                    fn(query, PER_SOURCE_LIMIT[name], seen_urls=seen[name], save=save),
                    timeout=3600,
                )
            except Exception as e:
                print(f"[ERROR] {name} failed:", e)
                return []

        results = await asyncio.gather(
            *(safe_fetch(fn, name) for name, _, fn in SOURCES)
        )
        found = sum(len(r) for r in results)
        total += found
        print(f"📊 [{category}] query {attempt + 1}: {found} new — run total {total}/{TARGET_PER_RUN}")

        if save:
            record_query_use(category, query, found)

    print(f"[{category}] Total NEW images collected: {total}")
    return total

async def run_cron():
    now = datetime.now(TZ)
    category, date_key, interval_index = get_current_category(now)

    if await already_ran(date_key):
        print(f"⏩ Already ran for {category} today.")
        return

    run_hour, run_minute = await get_or_create_schedule(category, date_key, interval_index)

    run_time = now.replace(hour=run_hour, minute=run_minute, second=0, microsecond=0)
    window_end = run_time + timedelta(minutes=5)

    if now < run_time:
        print(f"⏳ Too early: waiting for {run_time.strftime('%H:%M')}")
        return
    elif now >= window_end:
        print(f"❌ Too late: missed scheduled time {run_hour}:{run_minute}")
        return

    print(f"🚀 Running scraper for {category} at scheduled time")
    await fetch_all_for_category(category)
    await record_run(date_key, category)


async def run_cleanup():
    threshold_date = datetime.now(TZ).date().isoformat()  # e.g., '2025-05-10'
    supabase.from_("cron_schedule").delete().lt("created_at", threshold_date).execute()
    return {"status": "cleaned"}
