"""Time the anime list and the platform-wide aggregates, and EXPLAIN ANALYZE every statement.

    PYTHONPATH=. uv run --env-file .env.rds python analysis/bench_aggregates.py

Read-only. Runs the real repository queries on a pooled connection (so no HTTP layer), after
one warm-up, 15 timed runs per case. Prints no credentials.
"""

import asyncio
import re
import statistics
import time

from sqlalchemy import event

from app.catalogue.enums import AnimeSort, GroupSort, Order, StudioSort
from app.catalogue.read_repository import AggregateFilters, AnimeFilters, CatalogueReadRepository
from app.core.database.database import asyncSessionMaker, engine

RUNS = 15
NONE = AggregateFilters()
MIN_RATINGS = 500

CASES = {
    "list: all anime, by members": lambda r: r.search_anime(
        AnimeFilters(), AnimeSort.MEMBERS, None, 20, 0
    ),
    "list: all anime, by members, page 500": lambda r: r.search_anime(
        AnimeFilters(), AnimeSort.MEMBERS, None, 20, 10000
    ),
    "list: all anime, by title": lambda r: r.search_anime(
        AnimeFilters(), AnimeSort.TITLE, None, 20, 0
    ),
    "list: airing, by members": lambda r: r.search_anime(
        AnimeFilters(statuses=("Currently Airing",)), AnimeSort.MEMBERS, None, 20, 0
    ),
    "list: TV 2019, by score": lambda r: r.search_anime(
        AnimeFilters(types=("TV",), year=2019, min_ratings=500), AnimeSort.SCORE, None, 20, 0
    ),
    "list: tag 1 (Action), by score": lambda r: r.search_anime(
        AnimeFilters(tag_ids=(1,), min_ratings=500), AnimeSort.SCORE, None, 20, 0
    ),
    "list: tag 1 + year 2019, by score": lambda r: r.search_anime(
        AnimeFilters(tag_ids=(1,), year=2019, min_ratings=500), AnimeSort.SCORE, None, 20, 0
    ),
    "list: a studio, newest": lambda r: r.search_anime(
        AnimeFilters(studio_ids=(1,)), AnimeSort.NEWEST, None, 20, 0
    ),
    "list: search 'titan'": lambda r: r.search_anime(
        AnimeFilters(q="titan"), AnimeSort.RELEVANCE, None, 10, 0
    ),
    "list: search 'one piece'": lambda r: r.search_anime(
        AnimeFilters(q="one piece"), AnimeSort.RELEVANCE, None, 10, 0
    ),
    "list: search 'a' (broadest)": lambda r: r.search_anime(
        AnimeFilters(q="a"), AnimeSort.RELEVANCE, None, 10, 0
    ),
    "list: search 'zzzz' (no match)": lambda r: r.search_anime(
        AnimeFilters(q="zzzz"), AnimeSort.RELEVANCE, None, 10, 0
    ),
    "list: tags 1 AND 4": lambda r: r.search_anime(
        AnimeFilters(tag_ids=(1, 4)), AnimeSort.MEMBERS, None, 20, 0
    ),
    "list: tags 1 OR 4": lambda r: r.search_anime(
        AnimeFilters(tag_ids=(1, 4), tag_match_all=False), AnimeSort.MEMBERS, None, 20, 0
    ),
    "options: filters (all 78 tags + year range)": lambda r: r.list_tags(),
    "options: year range": lambda r: r.year_range(),
    "options: studios, by anime count": lambda r: r.list_studios(
        None, 1, StudioSort.ANIME_COUNT, None, 20, 0
    ),
    "options: studios, search 'toei'": lambda r: r.list_studios(
        "toei", 1, StudioSort.ANIME_COUNT, None, 20, 0
    ),
    "options: studios, search 'a' (broadest)": lambda r: r.list_studios(
        "a", 1, StudioSort.ANIME_COUNT, None, 20, 0
    ),
    "options: studios, by name, page 20": lambda r: r.list_studios(
        None, 1, StudioSort.NAME, None, 20, 400
    ),
    "by-year": lambda r: r.stats_by_year(NONE, MIN_RATINGS),
    "by-year, TV only": lambda r: r.stats_by_year(AggregateFilters(type="TV"), MIN_RATINGS),
    "by-type": lambda r: r.stats_by_type(NONE, MIN_RATINGS),
    "by-season": lambda r: r.stats_by_season(NONE, MIN_RATINGS),
    "by-tag": lambda r: r.stats_by_tag(NONE, MIN_RATINGS, GroupSort.ANIME_COUNT, Order.DESC),
    "by-tag, TV 2015-2025": lambda r: r.stats_by_tag(
        AggregateFilters(type="TV", year_from=2015, year_to=2025),
        MIN_RATINGS,
        GroupSort.MEAN_SCORE,
        Order.DESC,
    ),
    "by-studio": lambda r: r.stats_by_studio(
        NONE, MIN_RATINGS, 10, GroupSort.ANIME_COUNT, Order.DESC, 20, 0
    ),
    "by-studio, by mean score": lambda r: r.stats_by_studio(
        NONE, MIN_RATINGS, 10, GroupSort.MEAN_SCORE, Order.DESC, 20, 0
    ),
}


def plan_summary(plan: str) -> str:
    scans = re.findall(r"(Seq Scan|Index Only Scan|Index Scan|Bitmap Heap Scan) on (\w+)", plan)
    return ", ".join(f"{kind.split()[0]} {table}" for kind, table in dict.fromkeys(scans))


async def main():
    captured = []

    def after(conn, cursor, statement, parameters, context, executemany):
        if not statement.startswith("SET LOCAL"):
            captured.append((statement, parameters))

    event.listen(engine.sync_engine, "after_cursor_execute", after)
    print(f"{'case':38} {'round trip':>10} {'DB execution (ms) per statement':>34}   scans")
    for name, call in CASES.items():
        async with asyncSessionMaker() as session:
            repository = CatalogueReadRepository(session)
            await call(repository)  # warm-up
            times = []
            for _ in range(RUNS):
                captured.clear()
                started = time.perf_counter()
                await call(repository)
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
        print(
            f"{name:38} {statistics.median(times) * 1000:8.1f}ms "
            f"{' + '.join(f'{e:.2f}' for e in executions):>34}   {' | '.join(scans)}"
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
