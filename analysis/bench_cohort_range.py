"""Time and EXPLAIN ANALYZE the cohort query now that the cohort spans the years an anime aired.

    PYTHONPATH=. uv run --env-file .env.rds python analysis/bench_cohort_range.py

Read-only. Runs the real repository query for a short show and for long runners.
"""

import asyncio
import re
import statistics
import time

from sqlalchemy import event, text

from app.catalogue.repository import CatalogueRepository
from app.core.database.database import asyncSessionMaker, engine
from app.statistics.service import _current_year, cohort_years

IDS = {
    "Steins;Gate (2011)": 9253,
    "Boku no Hero Academia": 31964,
    "One Piece": 21,
    "Naruto": 20,
    "Gintama": 918,
}
RUNS = 15


async def main():
    captured = []

    def after(conn, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    event.listen(engine.sync_engine, "after_cursor_execute", after)
    async with engine.connect() as conn:
        indexes = (
            (
                await conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE tablename IN ('anime','review') "
                        "AND indexname LIKE 'ix_%'"
                    )
                )
            )
            .scalars()
            .all()
        )
    print("secondary indexes on RDS:", indexes)

    for name, mal_id in IDS.items():
        async with asyncSessionMaker() as session:
            repo = CatalogueRepository(session)
            found = await repo.get_anime_with_statistics(mal_id)
            anime = found[0]
            year_from, year_to = cohort_years(anime, _current_year())
            await repo.get_cohort_standing(mal_id, anime.type, year_from, year_to)  # warm-up
            times, standing = [], None
            for _ in range(RUNS):
                captured.clear()
                started = time.perf_counter()
                standing = await repo.get_cohort_standing(mal_id, anime.type, year_from, year_to)
                times.append(time.perf_counter() - started)
            statement, parameters = captured[-1]
            await session.rollback()
        async with engine.connect() as conn:
            plans = []
            for _ in range(5):
                result = await conn.exec_driver_sql(
                    "EXPLAIN (ANALYZE, BUFFERS) " + statement, parameters
                )
                plans.append("\n".join(line for (line,) in result.all()))
            await conn.rollback()
        exec_ms = statistics.median(
            float(re.search(r"Execution Time: ([\d.]+)", p).group(1)) for p in plans
        )
        scan = re.search(
            r"(Seq Scan|Index Scan|Bitmap Heap Scan|Index Only Scan)[^\n]*anime[^\n]*", plans[-1]
        )
        print(
            f"{name:24} {year_from}-{year_to}  cohort {standing.cohort_size:5}  "
            f"query round trip median {statistics.median(times) * 1000:6.1f} ms  "
            f"DB execution median {exec_ms:6.2f} ms  {scan.group(1) if scan else '?'}"
        )
        print(
            f"{'':24} drop rate #{standing.drop_rate_rank} of {standing.drop_rate_count} "
            f"(p={standing.drop_rate_percentile}), completion #{standing.completion_rate_rank} "
            f"of {standing.completion_rate_count} (p={standing.completion_rate_percentile})"
        )
    await engine.dispose()


asyncio.run(main())
