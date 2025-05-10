from dotenv import load_dotenv
from supabase import create_client
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
DB1_NAME=os.getenv("DB1_NAME")



supabase = create_client(supabase_url=SUPABASE_URL, supabase_key=SUPABASE_SERVICE_ROLE_KEY)

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
        supabase.table("image_queries").upsert(query_rows).execute()