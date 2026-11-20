"""Export the tables the Power BI dashboard uses to CSV (analysis/powerbi/data/).

    PYTHONPATH=. uv run --env-file .env.rds python analysis/export_powerbi_csv.py

Read-only. The two raw tables are plain SELECTs. The four ranked tables call the same repository
queries and the same thresholds as the API, so the dashboard shows the numbers the API serves:

    anime.csv             one row per anime (no synopsis or other long text)
    anime_statistics.csv  list-status counts per anime, with started and drop_rate
    by_year.csv           count and score by start year (/statistics/by-year)
    by_studio.csv         count and score per studio with 10+ scored anime (/statistics/by-studio)
    hidden_gems.csv       the hidden gems (/statistics/hidden-gems)
    most_dropped.csv      anime ranked by drop rate (/statistics/most-dropped)

No user data is exported: these are MAL catalogue numbers only. The CSVs are not committed.
"""

import asyncio
import csv
from pathlib import Path

from sqlalchemy import text

from app.catalogue.enums import GroupSort, Order
from app.catalogue.read_repository import AggregateFilters, CatalogueReadRepository
from app.core.database.database import asyncSessionMaker, engine
from app.statistics.aggregate_service import DEFAULT_MIN_ANIME, MIN_RATINGS
from app.statistics.list_service import (
    DROPPED_MIN_MEMBERS,
    HIDDEN_GEM_COHORT_MIN_RATINGS,
    HIDDEN_GEM_MEMBERS_PERCENTILE,
    HIDDEN_GEM_MIN_SCORED_BY,
    HIDDEN_GEM_SCORE_PERCENTILE,
)

OUT = Path(__file__).parent / "powerbi" / "data"
EVERYTHING = 1_000_000  # a limit larger than any table, so each list is exported whole

ANIME_SQL = """
    select mal_id, title, type, status, season, year, episodes, mal_score,
           scored_by_mal, members_mal, favorites_mal, rank_mal, popularity_mal
    from anime order by mal_id
"""
STATISTICS_SQL = """
    select anime_id, watching, completed, on_hold, dropped, plan_to_watch, total,
           total - plan_to_watch as started,
           case when total - plan_to_watch > 0
                then round((dropped::numeric / (total - plan_to_watch)), 4) end as drop_rate
    from anime_statistics_mal order by anime_id
"""


def write(name: str, rows: list[dict]) -> None:
    path = OUT / f"{name}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as file:  # BOM: Power BI reads UTF-8
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        # floats to 4 places, so a rate reads 0.5402 and not 0.5402488676684599
        writer.writerows(
            {k: round(v, 4) if isinstance(v, float) else v for k, v in row.items()} for row in rows
        )
    print(f"{name + '.csv':24} {len(rows):>7,} rows  {path.stat().st_size / 1024:>8,.0f} KiB")


async def raw(session, sql: str) -> list[dict]:
    return [dict(row._mapping) for row in (await session.execute(text(sql))).all()]


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    no_filter = AggregateFilters()
    async with asyncSessionMaker() as session:
        repository = CatalogueReadRepository(session)
        write("anime", await raw(session, ANIME_SQL))
        write("anime_statistics", await raw(session, STATISTICS_SQL))
        write("by_year", await repository.stats_by_year(no_filter, MIN_RATINGS))
        studios, _ = await repository.stats_by_studio(
            no_filter,
            MIN_RATINGS,
            DEFAULT_MIN_ANIME,
            GroupSort.ANIME_COUNT,
            Order.DESC,
            EVERYTHING,
            0,
        )
        write("by_studio", studios)
        gems, _ = await repository.hidden_gems(
            no_filter,
            HIDDEN_GEM_COHORT_MIN_RATINGS,
            HIDDEN_GEM_SCORE_PERCENTILE,
            HIDDEN_GEM_MEMBERS_PERCENTILE,
            HIDDEN_GEM_MIN_SCORED_BY,
            EVERYTHING,
            0,
        )
        write("hidden_gems", gems)
        dropped, _ = await repository.most_dropped(no_filter, DROPPED_MIN_MEMBERS, EVERYTHING, 0)
        write("most_dropped", dropped)
        await session.rollback()
    await engine.dispose()
    print(f"\nWritten to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
