import random, asyncio, os
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException

from cron_runner import run_cron

from dotenv import load_dotenv

from supabase_client import supabase

load_dotenv()

API_TOKEN = os.getenv("API_TOKEN")
CUSTOM_HEADER = "X-API-TOKEN"

app = FastAPI()

def verify_token(request: Request):
    token = request.headers.get(CUSTOM_HEADER)
    if token != API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid or missing token")


@app.post("/trigger")
async def trigger_cron(request: Request):
    verify_token(request)
    await run_cron()
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "Hello from FastAPI"}

@app.get("/hello")
async def hello(request: Request):
    verify_token(request)
    return {"message": "Hello World"}

@app.post("/cleanup")
async def cleanup_old_schedules(request: Request):
    verify_token(request)
    threshold_date = datetime.now().date().isoformat()  # e.g., '2025-05-10'
    supabase.from_("cron_schedule").delete().lt("substring(date_key from '^[0-9]{4}-[0-9]{2}-[0-9]{2}')", threshold_date).execute()
    return {"status": "cleaned"}