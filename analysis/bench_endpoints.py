"""End-to-end timing, statement count and response size of every public endpoint, plus the size
spread of the per-anime responses over a random sample.

    PYTHONPATH=. uv run --env-file .env.rds python analysis/bench_endpoints.py

Read-only. Runs the real app through its ASGI interface against RDS, so the times include the
network round trip from the machine running it (about 16 ms per statement from a laptop). Used for
the cache decisions in docs/METRICS.md section 11.
"""

import asyncio
import statistics
import time

import httpx
from sqlalchemy import event, text

from app.core.database.database import engine
from app.main import app

CASES = [
    ("list: default (limit 20)", "/api/v1/anime"),
    ("list: limit 100", "/api/v1/anime?limit=100"),
    (
        "list: airing + releasing, members",
        "/api/v1/anime?status=airing&release_status=releasing&sort=members",
    ),
    (
        "list: season fall 2025, by score",
        "/api/v1/anime?season=fall&year=2025&sort=score&min_ratings=500",
    ),
    ("list: tag 1, by score", "/api/v1/anime?tag_id=1&sort=score&min_ratings=500"),
    ("list: search 'titan'", "/api/v1/anime?q=titan"),
    ("list: sort by title", "/api/v1/anime?sort=title"),
    ("filters", "/api/v1/filters"),
    ("studios (20)", "/api/v1/studios"),
    ("by-year", "/api/v1/statistics/by-year"),
    ("by-type", "/api/v1/statistics/by-type"),
    ("by-season", "/api/v1/statistics/by-season"),
    ("by-tag", "/api/v1/statistics/by-tag"),
    ("by-studio (20)", "/api/v1/statistics/by-studio"),
    ("anime statistics: Steins;Gate", "/api/v1/anime/9253/statistics"),
    ("anime statistics: One Piece", "/api/v1/anime/21/statistics"),
    ("anime episodes: Steins;Gate", "/api/v1/anime/9253/statistics/episodes"),
    ("anime episodes: One Piece", "/api/v1/anime/21/statistics/episodes"),
]


async def main():
    count = [0]
    event.listen(
        engine.sync_engine, "after_cursor_execute", lambda *a: count.__setitem__(0, count[0] + 1)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60
    ) as c:
        print(f"{'request':38} {'statements':>10} {'median ms':>10} {'size KiB':>9}")
        for name, url in CASES:
            await c.get(url)
            times, size, stmts = [], 0, 0
            for _ in range(5):
                count[0] = 0
                t = time.perf_counter()
                r = await c.get(url)
                times.append((time.perf_counter() - t) * 1000)
                size, stmts = len(r.content), count[0]
                assert r.status_code == 200, (name, r.status_code)
            print(f"{name:38} {stmts:10} {statistics.median(times):10.0f} {size / 1024:9.1f}")
        async with engine.connect() as conn:
            ids = [
                r[0]
                for r in (
                    await conn.execute(
                        text(
                            "SELECT anime_id FROM anime_statistics_mal ORDER BY md5(anime_id::text) LIMIT 60"
                        )
                    )
                ).all()
            ]
            eids = [
                r[0]
                for r in (
                    await conn.execute(
                        text(
                            "SELECT anime_id FROM (SELECT DISTINCT anime_id FROM episode) t ORDER BY md5(anime_id::text) LIMIT 40"
                        )
                    )
                ).all()
            ]
        stats_sizes = [
            len((await c.get(f"/api/v1/anime/{i}/statistics")).content) / 1024 for i in ids
        ]
        ep_sizes = [
            len((await c.get(f"/api/v1/anime/{i}/statistics/episodes")).content) / 1024
            for i in eids
        ]

        def summ(xs):
            xs = sorted(xs)
            return f"avg {statistics.mean(xs):.1f}, median {statistics.median(xs):.1f}, p95 {xs[int(len(xs) * 0.95) - 1]:.1f}, max {xs[-1]:.1f} KiB (n={len(xs)})"

        print("\nper-anime statistics, random sample:", summ(stats_sizes))
        print("per-anime episodes, random sample:   ", summ(ep_sizes))
    await engine.dispose()


asyncio.run(main())
