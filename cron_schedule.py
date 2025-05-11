import os
from datetime import datetime
from zoneinfo import ZoneInfo

from cron_runner import run_cron, run_cleanup

ENV = os.getenv("APP_ENV", "dev")

async def cron_run_getter():
    if ENV != "prod":
        print(f"Not in production env (getter) {datetime.now(ZoneInfo("Africa/Porto-Novo"))}")
        return
    print(f"⏱️ Running run_cron at {datetime.now(ZoneInfo("Africa/Porto-Novo"))}")
    await run_cron()

async def run_test():
    print(f"⏱️ Running testst at {datetime.now(ZoneInfo("Africa/Porto-Novo"))}")
    return

#@repeat_at(cron="0 1 * * *")

async def cron_run_cleanup():
    if ENV != "prod":
        print(f"Not in production env (cleanup) {datetime.now(ZoneInfo("Africa/Porto-Novo"))}")
        return
    print(f"⏱️ Running run_cron at {datetime.now(ZoneInfo("Africa/Porto-Novo"))}")
    await run_cleanup()


