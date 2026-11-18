"""Time hidden-gems and most-dropped, EXPLAIN ANALYZE every statement, and save the real rows.

    PYTHONPATH=. uv run --env-file .env.rds python analysis/bench_lists.py OUT.json

Read-only. Runs the real repository queries on a pooled connection (so no HTTP layer), after
one warm-up, 15 timed runs per case. Prints no credentials. OUT.json gets the plans' summary,
the timings and the result rows of each case, for the write-up.
"""

import asyncio
import json
import re
import statistics
import sys
import time

from sqlalchemy import event

from app.catalogue.read_repository import AggregateFilters, CatalogueReadRepository
from app.core.database.database import asyncSessionMaker, engine
from app.statistics.list_service import (
    DROPPED_MIN_MEMBERS,
    HIDDEN_GEM_COHORT_MIN_RATINGS,
    HIDDEN_GEM_MEMBERS_PERCENTILE,
    HIDDEN_GEM_MIN_SCORED_BY,
    HIDDEN_GEM_SCORE_PERCENTILE,
)

RUNS = 15
NONE = AggregateFilters()
TV = AggregateFilters(type="TV")
MOVIE = AggregateFilters(type="Movie")
TV_RECENT = AggregateFilters(type="TV", year_from=2015, year_to=2025)


def gems(filters, cohort=HIDDEN_GEM_COHORT_MIN_RATINGS, limit=20, offset=0):
    return lambda r: r.hidden_gems(
        filters,
        cohort,
        HIDDEN_GEM_SCORE_PERCENTILE,
        HIDDEN_GEM_MEMBERS_PERCENTILE,
        HIDDEN_GEM_MIN_SCORED_BY,
        limit,
        offset,
    )


def dropped(filters, floor=DROPPED_MIN_MEMBERS, limit=20, offset=0):
    return lambda r: r.most_dropped(filters, floor, limit, offset)


CASES = {
    "hidden-gems: default": gems(NONE),
    "hidden-gems: type=tv": gems(TV),
    "hidden-gems: type=movie": gems(MOVIE),
    "hidden-gems: tv 2015-2025": gems(TV_RECENT),
    "hidden-gems: min_ratings=0": gems(NONE, cohort=0),
    "hidden-gems: min_ratings=500": gems(NONE, cohort=500),
    "hidden-gems: page 3 (offset 40)": gems(NONE, offset=40),
    "most-dropped: default": dropped(NONE),
    "most-dropped: type=tv": dropped(TV),
    "most-dropped: type=movie": dropped(MOVIE),
    "most-dropped: tv 2015-2025": dropped(TV_RECENT),
    "most-dropped: min_members=1000": dropped(NONE, floor=1000),
    "most-dropped: min_members=100000": dropped(NONE, floor=100_000),
    "most-dropped: page 3 (offset 40)": dropped(NONE, offset=40),
}


def plan_summary(plan: str) -> str:
    scans = re.findall(r"(Seq Scan|Index Only Scan|Index Scan|Bitmap Heap Scan) on (\w+)", plan)
    return ", ".join(f"{kind.split()[0]} {table}" for kind, table in dict.fromkeys(scans))


async def main(out_path: str):
    captured = []

    def after(conn, cursor, statement, parameters, context, executemany):
        if not statement.startswith("SET LOCAL"):
            captured.append((statement, parameters))

    event.listen(engine.sync_engine, "after_cursor_execute", after)
    report = {}
    print(f"{'case':36} {'round trip':>10} {'DB execution (ms) per statement':>34}   scans")
    for name, call in CASES.items():
        async with asyncSessionMaker() as session:
            repository = CatalogueReadRepository(session)
            await call(repository)  # warm-up
            times = []
            for _ in range(RUNS):
                captured.clear()
                started = time.perf_counter()
                rows, total = await call(repository)
                times.append(time.perf_counter() - started)
            statements = list(captured)
            await session.rollback()
        executions, scans = [], []
        async with engine.connect() as conn:
            for statement, parameters in statements:
                runs = []
                for _ in range(5):
                    result = await conn.exec_driver_sql(
                        "EXPLAIN (ANALYZE, BUFFERS) " + statement, parameters
                    )
                    plan = "\n".join(line for (line,) in result.all())
                    runs.append(float(re.search(r"Execution Time: ([\d.]+)", plan).group(1)))
                executions.append(statistics.median(runs))
                scans.append(plan_summary(plan))
            await conn.rollback()
        round_trip = statistics.median(times) * 1000
        print(
            f"{name:36} {round_trip:8.1f}ms "
            f"{' + '.join(f'{e:.2f}' for e in executions):>34}   {' | '.join(scans)}"
        )
        report[name] = {
            "round_trip_ms": round(round_trip, 1),
            "execution_ms": [round(e, 2) for e in executions],
            "scans": scans,
            "total": total,
            "rows": rows,
        }
    await engine.dispose()
    with open(out_path, "w", encoding="utf-8") as out:
        json.dump(report, out, indent=1, default=str)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
