#!/usr/bin/env python
"""Test harness for the scraper.

Runs the real fetchers against the real sites, but by default writes nothing to
Supabase. It reports the only metric that matters here: how many of the images
a run returns are actually NEW relative to what is already stored.

    python scripts/dry_run.py --source db1 --limit 10
    python scripts/dry_run.py --source db1 --runs 2          # novelty between runs
    python scripts/dry_run.py --source all --headful         # watch the browser
    python scripts/dry_run.py --source db1 --limit 50 --save # really write

Exit code is 1 when a run finds no new image, so it can be used in CI.
"""

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from queries import build_query_space, pick_query, record_query_use
from supabase_client import fetch_known_image_urls

SOURCES = {
    "db1": ("DB1_NAME", "getter.db1_fetch", "fetch_db1_urls"),
    "db2": ("DB2_NAME", "getter.db2_fetch", "fetch_db2_image_urls"),
    "db3": ("DB3_NAME", "getter.db3_fetch", "fetch_db3_urls"),
    "db4": ("DB4_NAME", "getter.db4_fetch", "fetch_db4_urls"),
}


def load_fetcher(key: str):
    import importlib

    env_name, module_path, fn_name = SOURCES[key]
    module = importlib.import_module(module_path)
    return os.getenv(env_name) or key, getattr(module, fn_name)


async def run_source(key: str, query: str, limit: int, save: bool, runs: int) -> dict:
    source_name, fetch = load_fetcher(key)
    known = fetch_known_image_urls(source_name)
    baseline = len(known)

    per_run = []
    all_returned: list[set[str]] = []

    for i in range(runs):
        started = time.monotonic()
        # Each run sees the images stored before it PLUS whatever the previous
        # run in this session returned — that is what the cron experiences.
        seen = set(known)
        for previous in all_returned:
            seen |= previous

        urls = await fetch(query, limit, seen_urls=seen, save=save)
        elapsed = time.monotonic() - started

        returned = set(urls)
        overlap_db = len(returned & known)
        overlap_prev = len(returned & set().union(*all_returned)) if all_returned else 0
        all_returned.append(returned)
        per_run.append(
            {
                "run": i + 1,
                "returned": len(returned),
                "overlap_db": overlap_db,
                "overlap_prev_runs": overlap_prev,
                "seconds": round(elapsed, 1),
            }
        )
        print(
            f"   run {i+1}/{runs}: {len(returned)} images | "
            f"déjà en base: {overlap_db} | déjà vues ce test: {overlap_prev} | {elapsed:.1f}s"
        )

    union = set().union(*all_returned) if all_returned else set()
    return {
        "source": source_name,
        "baseline_known": baseline,
        "runs": per_run,
        "distinct_total": len(union),
        "urls": sorted(union),
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="db1", choices=["db1", "db2", "db3", "db4", "all"])
    parser.add_argument("--category", default="All")
    parser.add_argument("--query", default=None, help="bypass the rotation and force a query")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--runs", type=int, default=1, help="consecutive runs, to measure novelty")
    parser.add_argument("--save", action="store_true", help="write to Supabase (off by default)")
    parser.add_argument("--record-query", action="store_true", help="mark the query as used")
    parser.add_argument("--headful", action="store_true", help="show the browser window")
    parser.add_argument("--out", default=None, help="write every URL found to this JSON file")
    args = parser.parse_args()

    if args.headful:
        os.environ["HEADLESS"] = "0"

    space = build_query_space(args.category)
    query = args.query or pick_query(args.category)
    print(f"📚 Query space for {args.category!r}: {len(space)} combinations")
    print(f"🔎 Query: {query!r}")
    print(f"💾 Writes to Supabase: {'YES' if args.save else 'no (dry run)'}\n")

    keys = ["db1", "db2", "db3", "db4"] if args.source == "all" else [args.source]
    reports = []
    for key in keys:
        print(f"── {key} ──")
        reports.append(await run_source(key, query, args.limit, args.save, args.runs))
        print()

    total_new = sum(r["distinct_total"] for r in reports)

    print("══ RÉSUMÉ ══")
    for r in reports:
        runs = r["runs"]
        db_overlap = sum(x["overlap_db"] for x in runs)
        prev_overlap = sum(x["overlap_prev_runs"] for x in runs)
        print(
            f"{r['source']:<12} connues avant: {r['baseline_known']:<6} "
            f"nouvelles distinctes: {r['distinct_total']:<4} "
            f"doublons base: {db_overlap} doublons inter-runs: {prev_overlap}"
        )
        for url in r["urls"]:
            print(f"             {url}")

    if args.out:
        import json

        payload = {"query": query, "category": args.category, "sources": reports}
        with open(args.out, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\n💾 URLs written to {args.out}")

    if args.record_query and args.save:
        record_query_use(args.category, query, total_new)
        print(f"📝 Query recorded in search_queries ({total_new} new images)")

    if total_new == 0:
        print("\n❌ Aucune image nouvelle — le sélecteur, le site ou la requête est à revoir.")
        return 1
    print(f"\n✅ {total_new} image(s) nouvelle(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
