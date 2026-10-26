"""Measure how the clean job's write cost changes with batch size.

    PYTHONPATH=. BENCH_DIR=<dir> uv run --env-file .env.rds python bench_clean.py sample
    PYTHONPATH=. BENCH_DIR=<dir> BENCH_URL=<url> uv run ... python bench_clean.py run <name> [sizes...]

All writes go to TEMPORARY tables that shadow the real ones for one pinned connection.
"""

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

TMP = Path(os.environ["BENCH_DIR"])
SAMPLE = TMP / "sample.json"
TABLES = [
    "anime", "tag", "studio", "anime_tag", "anime_studio", "anime_relation",
    "anime_statistics_mal", "episode", "review", "raw_tenrai",
]  # fmt: skip
DEFAULT_SIZES = [1, 10, 50, 100, 250, 500, 1000]
SAMPLE_ANIME = 500


async def build_sample():
    from app.core.database.database import asyncSessionMaker
    from app.ingestion.enums.resource_enum import Resource
    from app.ingestion.storage.raw_store import RawStore

    store = RawStore.from_settings()
    async with asyncSessionMaker() as s:
        ids = [
            r[0]
            for r in (
                await s.execute(
                    text("select mal_id from anime order by md5(mal_id::text) limit :n"),
                    {"n": SAMPLE_ANIME},
                )
            ).all()
        ]
        index = (
            await s.execute(
                text(
                    "select mal_id, resource from raw_tenrai where mal_id = any(:ids) order by 1, 2"
                ),
                {"ids": ids},
            )
        ).all()
    sem = asyncio.Semaphore(16)

    async def load(mal_id, resource):
        async with sem:
            payload = await store.get(mal_id, Resource(resource))
        return {"mal_id": mal_id, "resource": Resource(resource).value, "payload": payload}

    rows = await asyncio.gather(*(load(m, r) for m, r in index))
    SAMPLE.write_text(json.dumps(rows))
    by_res = {}
    for r in rows:
        by_res[r["resource"]] = by_res.get(r["resource"], 0) + 1
    print(
        f"sample: {len(ids)} anime, {len(rows)} raw rows {by_res}, {SAMPLE.stat().st_size / 1e6:.1f} MB"
    )


class CacheStore:
    def __init__(self, rows):
        self.cache = {(r["mal_id"], r["resource"]): r["payload"] for r in rows}

    async def get(self, mal_id, resource):
        from app.ingestion.enums.resource_enum import Resource

        return self.cache[(mal_id, Resource(resource).value)]


class DummyMetrics:
    async def start_run(self, job):
        return 1

    async def finish_run(self, *args, **kwargs):
        return None

    async def commit(self):
        return None

    async def last_succeeded_run_start(self, job):
        return None


async def run(name, url, sizes):
    from app.catalogue.repository import CatalogueRepository
    from app.ingestion.jobs.clean_raw_job import CleanAnimeJob
    from app.ingestion.repository.raw_tenrai_repository import RawTenraiRepository

    rows = json.loads(SAMPLE.read_text())
    store = CacheStore(rows)

    # one connection for everything, so the temporary tables stay visible
    engine = create_async_engine(
        url, pool_size=1, max_overflow=0, pool_pre_ping=False, connect_args={"command_timeout": 600}
    )
    counts = {"stmt": 0, "commit": 0}

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count_statement(conn, cursor, statement, parameters, context, executemany):
        counts["stmt"] += 1

    @event.listens_for(engine.sync_engine, "commit")
    def _count_commit(conn):
        counts["commit"] += 1

    # BENCH_REAL=1 writes to the real tables (with their foreign keys and write-ahead logging)
    # instead of temporary copies. They are emptied first, so it only runs on a test database.
    real = os.environ.get("BENCH_REAL") == "1"
    if real and "test" not in (engine.url.database or ""):
        raise SystemExit("BENCH_REAL=1 truncates the tables: the database name must contain 'test'")
    async with engine.begin() as conn:
        if not real:
            for table in TABLES:
                await conn.execute(
                    text(f"create temp table {table} (like public.{table} including all)")
                )
        rtts = []
        for _ in range(30):
            started = time.perf_counter()
            await conn.execute(text("select 1"))
            rtts.append((time.perf_counter() - started) * 1000)
    rtt = statistics.median(rtts)
    print(
        f"[{name}] round trip (select 1): median {rtt:.1f} ms, min {min(rtts):.1f}, max {max(rtts):.1f}",
        flush=True,
    )

    results = []
    for size in sizes:
        async with engine.begin() as conn:
            await conn.execute(text("truncate " + ", ".join(TABLES)))
            await conn.execute(
                text("insert into raw_tenrai (mal_id, resource, size_bytes) values (:m, :r, 1)"),
                [{"m": r["mal_id"], "r": r["resource"]} for r in rows],
            )
        counts.update(stmt=0, commit=0)
        started = time.perf_counter()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            job = CleanAnimeJob(
                CatalogueRepository(session), RawTenraiRepository(session), DummyMetrics(), store
            )
            summary = await job.full_clean_loop(batch_size=size, full_pass=True)
        elapsed = time.perf_counter() - started
        n = summary["rows_read"]
        result = {
            "env": name, "batch_size": size, "rows": n, "batches": summary["batches"],
            "statements": counts["stmt"], "commits": counts["commit"],
            "stmt_per_row": round(counts["stmt"] / n, 3),
            "round_trips_per_row": round((counts["stmt"] + counts["commit"]) / n, 3),
            "seconds": round(elapsed, 2), "ms_per_row": round(elapsed / n * 1000, 2),
            "anime_changed": summary["anime_changed"], "episodes": summary["episodes_written"],
            "reviews": summary["reviews_written"], "parse_failed": summary["parse_failed"],
            "rtt_ms": round(rtt, 1),
        }  # fmt: skip
        results.append(result)
        print(json.dumps(result), flush=True)
        (TMP / f"results_{name}.json").write_text(json.dumps(results, indent=1))
    await engine.dispose()


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "sample":
        asyncio.run(build_sample())
    else:
        name = sys.argv[2]
        sizes = [int(x) for x in sys.argv[3:]] or DEFAULT_SIZES
        asyncio.run(run(name, os.environ["BENCH_URL"], sizes))
