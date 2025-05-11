import random, asyncio, os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request, HTTPException

from cron_runner import run_cron, run_cleanup

from dotenv import load_dotenv

from cron_schedule import cron_run_getter, cron_run_cleanup

load_dotenv()

API_TOKEN = os.getenv("API_TOKEN")
CUSTOM_HEADER = "X-API-TOKEN"

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the scheduler
    scheduler = AsyncIOScheduler()

    # Schedule run_getter_job every 1 minute (as per original repeat_every(seconds=60 * 1))
    scheduler.add_job(cron_run_getter, "interval", minutes=5)

    # Schedule cleanup_job every day at 1:00 AM
    scheduler.add_job(cron_run_cleanup, "cron", hour=1, minute=0)

    # Start the scheduler
    scheduler.start()

    try:
        yield
    finally:
        # Shutdown the scheduler gracefully on app shutdown
        scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

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
    await run_cleanup()
    return {"status": "ok"}


