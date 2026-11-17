"""Time the per-anime statistics endpoint's service, and EXPLAIN ANALYZE each of its queries.

    PYTHONPATH=. uv run --env-file .env.rds python analysis/bench_anime_statistics.py

Read-only: SELECTs and EXPLAIN ANALYZE of SELECTs. Runs the real StatisticsService on a pooled
connection, so the figures are the service's time without the HTTP layer. Prints no credentials.
"""

import asyncio
import statistics
import time

from sqlalchemy import event, text

from app.catalogue.repository import CatalogueRepository
from app.core.database.database import asyncSessionMaker, engine
from app.statistics.service import StatisticsService

RUNS = 15
QUERY_NAMES = ["anime+statistics", "tags", "studios", "reviews", "cohort"]


async def pick_samples() -> dict[str, int]:
    """Anime that stress different parts of the endpoint. Chosen from the data, not by hand."""
    queries = {
        # the biggest (type, year) cohort, at both ends of its popularity
        "big cohort, most popular": """
            WITH c AS (SELECT type, year FROM anime WHERE type IS NOT NULL AND year IS NOT NULL
                       GROUP BY 1, 2 ORDER BY count(*) DESC LIMIT 1)
            SELECT a.mal_id FROM anime a JOIN c ON a.type = c.type AND a.year = c.year
            JOIN anime_statistics_mal s ON s.anime_id = a.mal_id
            ORDER BY a.members_mal DESC NULLS LAST LIMIT 1""",
        "big cohort, least popular": """
            WITH c AS (SELECT type, year FROM anime WHERE type IS NOT NULL AND year IS NOT NULL
                       GROUP BY 1, 2 ORDER BY count(*) DESC LIMIT 1)
            SELECT a.mal_id FROM anime a JOIN c ON a.type = c.type AND a.year = c.year
            JOIN anime_statistics_mal s ON s.anime_id = a.mal_id
            ORDER BY a.members_mal ASC NULLS LAST LIMIT 1""",
        "most reviews": """
            SELECT anime_id FROM review GROUP BY anime_id ORDER BY count(*) DESC LIMIT 1""",
        "small cohort (<30)": """
            SELECT a.mal_id FROM anime a JOIN anime_statistics_mal s ON s.anime_id = a.mal_id
            WHERE (a.type, a.year) IN (SELECT type, year FROM anime WHERE type IS NOT NULL
                  AND year IS NOT NULL GROUP BY 1, 2 HAVING count(*) BETWEEN 5 AND 29)
            LIMIT 1""",
        "no statistics row": """
            SELECT a.mal_id FROM anime a LEFT JOIN anime_statistics_mal s ON s.anime_id = a.mal_id
            WHERE s.anime_id IS NULL LIMIT 1""",
    }
    picked = {}
    async with engine.connect() as conn:
        for name, sql in queries.items():
            mal_id = (await conn.execute(text(sql))).scalar_one_or_none()
            if mal_id is not None:
                picked[name] = mal_id
    return picked


class QueryRecorder:
    """Collects (statement, parameters, seconds) for every statement the service runs."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, float]] = []
        self._start = 0.0

    def before(self, conn, cursor, statement, parameters, context, executemany):
        self._start = time.perf_counter()

    def after(self, conn, cursor, statement, parameters, context, executemany):
        self.calls.append((statement, parameters, time.perf_counter() - self._start))


async def run_once(mal_id: int) -> tuple[float, float, int]:
    """One full service call on a fresh session: (service seconds, serialise seconds, bytes)."""
    async with asyncSessionMaker() as session:
        service = StatisticsService(CatalogueRepository(session))
        started = time.perf_counter()
        response = await service.get_anime_statistics(mal_id)
        service_seconds = time.perf_counter() - started
        await session.rollback()
    started = time.perf_counter()
    body = response.model_dump_json()
    return service_seconds, time.perf_counter() - started, len(body.encode())


def ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f}"


async def main():
    recorder = QueryRecorder()
    event.listen(engine.sync_engine, "before_cursor_execute", recorder.before)
    event.listen(engine.sync_engine, "after_cursor_execute", recorder.after)

    # round trip: how long one trivial query takes from here to the database
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))  # opens the connection
        trips = []
        for _ in range(30):
            started = time.perf_counter()
            await conn.execute(text("SELECT 1"))
            trips.append(time.perf_counter() - started)
    print(
        f"round trip SELECT 1: median {ms(statistics.median(trips))} ms, "
        f"min {ms(min(trips))} ms, max {ms(max(trips))} ms"
    )

    async with engine.connect() as conn:
        sizes = (
            await conn.execute(
                text(
                    "SELECT (SELECT count(*) FROM anime), (SELECT count(*) FROM anime_statistics_mal), "
                    "(SELECT count(*) FROM review), (SELECT count(*) FROM anime_tag), "
                    "(SELECT count(*) FROM anime_studio)"
                )
            )
        ).one()
    print("rows: anime={} statistics={} review={} anime_tag={} anime_studio={}".format(*sizes))

    samples = await pick_samples()
    print(f"samples: {samples}\n")

    for name, mal_id in samples.items():
        await run_once(mal_id)  # warm-up: connection, caches
        service_times, serialise_times, per_query = [], [], [[] for _ in QUERY_NAMES]
        size = 0
        for _ in range(RUNS):
            recorder.calls.clear()
            service_seconds, serialise_seconds, size = await run_once(mal_id)
            service_times.append(service_seconds)
            serialise_times.append(serialise_seconds)
            for index, (_, _, seconds) in enumerate(recorder.calls[: len(QUERY_NAMES)]):
                per_query[index].append(seconds)
        queries = len(recorder.calls)
        print(f"## {name} (mal_id {mal_id}): {queries} queries, response {size / 1024:.1f} KiB")
        print(
            f"service: median {ms(statistics.median(service_times))} ms, "
            f"p95 {ms(sorted(service_times)[int(RUNS * 0.95) - 1])} ms, "
            f"max {ms(max(service_times))} ms; serialise median "
            f"{ms(statistics.median(serialise_times))} ms"
        )
        for query_name, times in zip(QUERY_NAMES, per_query, strict=False):
            if times:
                print(f"  {query_name:18} median {ms(statistics.median(times))} ms")
        print()

    # EXPLAIN ANALYZE of every statement one call runs, on the big-cohort case
    target = samples.get("big cohort, most popular") or next(iter(samples.values()))
    recorder.calls.clear()
    await run_once(target)
    statements = list(recorder.calls)
    print(f"# EXPLAIN (ANALYZE, BUFFERS) for mal_id {target}")
    async with engine.connect() as conn:
        for query_name, (statement, parameters, _) in zip(QUERY_NAMES, statements, strict=False):
            print(f"\n## {query_name}")
            result = await conn.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS) " + statement, parameters
            )
            for (line,) in result.all():
                print(line)
        await conn.rollback()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
