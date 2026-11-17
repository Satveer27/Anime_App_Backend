"""Measure whether review(anime_id) and anime(type, year) speed up the statistics endpoint.

    PYTHONPATH=. uv run --env-file .env.rds python analysis/bench_statistics_indexes.py

Nothing real changes: `review` and `anime` are copied into TEMPORARY tables of the same name
on one pinned connection, which shadow the real ones, and the indexes are built on the copies.
The statements measured are the ones the service really runs (recorded from it).
"""

import asyncio
import re
import statistics

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalogue.repository import CatalogueRepository
from app.core.database.database import engine
from app.statistics.service import StatisticsService

RUNS = 9
# the most popular anime of the biggest cohort, and the anime with the most reviews
TARGETS = {"31964 (cohort 378, 20 reviews)": 31964, "61990 (most reviews)": 61990}


async def recorded_statements(conn, mal_id):
    calls = []

    def after(conn_, cursor, statement, parameters, context, executemany):
        calls.append((statement, parameters))

    event.listen(engine.sync_engine, "after_cursor_execute", after)
    try:
        async with AsyncSession(bind=conn) as session:
            await StatisticsService(CatalogueRepository(session)).get_anime_statistics(mal_id)
    finally:
        event.remove(engine.sync_engine, "after_cursor_execute", after)
    return {"reviews": calls[3], "cohort": calls[4]}


async def execution_ms(conn, statement, parameters) -> float:
    times = []
    for _ in range(RUNS):
        result = await conn.exec_driver_sql("EXPLAIN (ANALYZE) " + statement, parameters)
        text_plan = "\n".join(line for (line,) in result.all())
        times.append(float(re.search(r"Execution Time: ([\d.]+) ms", text_plan).group(1)))
    return statistics.median(times)


async def measure(conn, label):
    print(f"\n### {label}")
    for target, mal_id in TARGETS.items():
        statements = await recorded_statements(conn, mal_id)
        for name, (statement, parameters) in statements.items():
            print(f"{target:34} {name:8} {await execution_ms(conn, statement, parameters):7.2f} ms")


async def main():
    async with engine.connect() as conn:
        await conn.execute(text("CREATE TEMP TABLE review AS SELECT * FROM review"))
        await conn.execute(text("CREATE TEMP TABLE anime AS SELECT * FROM anime"))
        await conn.execute(text("ANALYZE review"))
        await conn.execute(text("ANALYZE anime"))
        await measure(conn, "temporary copies, no secondary index (should match the real tables)")

        await conn.execute(text("CREATE INDEX review_anime_id_idx ON review (anime_id)"))
        await conn.execute(text("ANALYZE review"))
        await measure(conn, "with review (anime_id)")

        await conn.execute(text("CREATE INDEX anime_type_year_idx ON anime (type, year)"))
        await conn.execute(text("ANALYZE anime"))
        await measure(conn, "with review (anime_id) and anime (type, year)")

        for index in ("review_anime_id_idx", "anime_type_year_idx"):
            size = (await conn.execute(text("SELECT pg_relation_size(:i)"), {"i": index})).scalar()
            print(f"size of {index}: {size / 1024:.0f} KiB")
        sizes = (
            await conn.execute(text("SELECT pg_relation_size('review'), pg_relation_size('anime')"))
        ).one()
        print(
            f"table sizes: review {sizes[0] / 1024 / 1024:.1f} MiB, anime {sizes[1] / 1024 / 1024:.1f} MiB"
        )
        await conn.rollback()  # the temporary tables go away with the connection
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
