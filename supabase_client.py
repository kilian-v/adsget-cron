from dotenv import load_dotenv
from supabase import create_client
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
DB1_NAME = os.getenv("DB1_NAME")


supabase = create_client(supabase_url=SUPABASE_URL, supabase_key=SUPABASE_SERVICE_ROLE_KEY)


def fetch_known_image_urls(source: str | None = None, limit: int = 50000) -> set[str]:
    """Image URLs already stored, so a run can skip what it has already seen.

    Without this the scraper re-collected the same top-of-feed images on every
    run and the DB-level dedup silently dropped them, so each run added ~0 new
    images. Passing `source=None` loads every source.
    """
    known: set[str] = set()
    page_size = 1000
    offset = 0
    try:
        while offset < limit:
            query = supabase.table("inspiration_images").select("image_url")
            if source:
                query = query.eq("source", source)
            res = query.range(offset, offset + page_size - 1).execute()
            rows = res.data or []
            known.update(row["image_url"] for row in rows if row.get("image_url"))
            if len(rows) < page_size:
                break
            offset += page_size
    except Exception as e:
        print(f"⚠️ Could not load known image URLs for {source}: {e}")
    print(f"🧠 Known images for {source or 'all sources'}: {len(known)}")
    return known


def upsert_image_with_query(image_url: str, queries: list[str], source: str = "unknown"):
    # Ensure the image exists or insert it
    result = supabase.table("inspiration_images").select("id").eq("image_url", image_url).execute()

    if result.data:
        image_id = result.data[0]["id"]
    else:
        insert_res = supabase.table("inspiration_images").insert({
            "image_url": image_url,
            "source": source
        }).execute()
        image_id = insert_res.data[0]["id"]

    # Prepare upserts for all queries
    query_rows = [{"image_id": image_id, "query": q} for q in queries]

    if query_rows:
        supabase.table("image_queries").upsert(query_rows, on_conflict="image_id,query").execute()
