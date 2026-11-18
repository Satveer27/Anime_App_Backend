from decimal import Decimal

import pytest

from app.catalogue.models import STATUS_NOT_YET_AIRED, Anime, AnimeStatisticsMal
from app.catalogue.read_repository import AggregateFilters, CatalogueReadRepository

pytestmark = pytest.mark.postgres

NO_FILTERS = AggregateFilters()


def gems(repo, filters=NO_FILTERS, cohort=50, min_scored_by=2000, limit=20, offset=0):
    return repo.hidden_gems(filters, cohort, 0.80, 0.50, min_scored_by, limit, offset)


async def add_anime(session, mal_id, **fields):
    defaults = {
        "title": f"Anime {mal_id}",
        "type": "TV",
        "year": 2015,
        "status": "Finished Airing",
        "mal_score": Decimal("7.00"),
        "scored_by_mal": 3000,
        "members_mal": 20000,
    }
    defaults.update(fields)
    session.add(Anime(mal_id=mal_id, **defaults))


async def add_list_stats(session, anime_id, *, total, plan_to_watch, dropped):
    session.add(
        AnimeStatisticsMal(
            anime_id=anime_id,
            watching=0,
            completed=0,
            on_hold=0,
            dropped=dropped,
            plan_to_watch=plan_to_watch,
            total=total,
        )
    )


# --- hidden gems -----------------------------------------------------------------------


async def seed_gems(session):
    """Ten TV anime scored 5.0 to 9.5, and two movies, so each type has its own ranking.

    TV 10 (9.5) is the only TV gem: top score, few members. TV 9 (9.0) is also top 20% by score
    but is one of the most watched, so it is not hidden. Movie 101 is the movie gem.
    """
    for i in range(1, 11):
        await add_anime(
            session,
            i,
            mal_score=Decimal("5.00") + Decimal("0.5") * (i - 1),
            members_mal={9: 500_000, 10: 3000}.get(i, 5000 + i * 1000),
            year=2020 if i == 10 else 2015,
        )
    await add_anime(session, 101, type="Movie", mal_score=Decimal("9.00"), members_mal=4000)
    await add_anime(session, 102, type="Movie", mal_score=Decimal("5.00"), members_mal=90000)
    # Not rankable: no score, and no type.
    await add_anime(session, 200, mal_score=None)
    await add_anime(session, 201, type=None)
    await session.commit()


async def test_hidden_gems_are_top_score_and_low_members_within_each_type(pg_session):
    await seed_gems(pg_session)

    rows, total = await gems(CatalogueReadRepository(pg_session))

    assert [row["mal_id"] for row in rows] == [10, 101]  # best score first
    assert total == 2
    assert rows[0]["score_percentile"] == 1.0
    assert rows[0]["members_percentile"] <= 0.5
    assert rows[0]["type"] == "TV"
    assert rows[0]["mal_score"] == 9.5


async def test_a_widely_watched_top_scorer_is_not_a_hidden_gem(pg_session):
    await seed_gems(pg_session)

    rows, _ = await gems(CatalogueReadRepository(pg_session))

    assert 9 not in [row["mal_id"] for row in rows]


async def test_the_ratings_floor_excludes_a_gem_with_too_few_ratings(pg_session):
    await seed_gems(pg_session)
    (await pg_session.get(Anime, 10)).scored_by_mal = 1999
    await pg_session.commit()

    rows, total = await gems(CatalogueReadRepository(pg_session))

    assert [row["mal_id"] for row in rows] == [101]
    assert total == 1


async def test_the_type_filter_narrows_the_result_without_changing_the_ranking(pg_session):
    await seed_gems(pg_session)
    repo = CatalogueReadRepository(pg_session)

    movies, _ = await gems(repo, AggregateFilters(type="Movie"))
    tv, _ = await gems(repo, AggregateFilters(type="TV"))

    assert [row["mal_id"] for row in movies] == [101]
    assert [row["mal_id"] for row in tv] == [10]


async def test_year_filters_narrow_the_result_but_keep_the_percentiles(pg_session):
    await seed_gems(pg_session)
    repo = CatalogueReadRepository(pg_session)

    only_2020, _ = await gems(repo, AggregateFilters(type="TV", year_from=2020))
    before_2020, total = await gems(repo, AggregateFilters(type="TV", year_to=2019))

    assert [row["mal_id"] for row in only_2020] == [10]
    assert only_2020[0]["score_percentile"] == 1.0  # still ranked against all TV, not just 2020
    assert before_2020 == [] and total == 0


async def test_the_cohort_minimum_decides_who_is_ranked(pg_session):
    await seed_gems(pg_session)
    # Ten barely rated TV anime with a higher score than anything else.
    for i in range(300, 310):
        await add_anime(pg_session, i, mal_score=Decimal("9.90"), scored_by_mal=10, members_mal=1)
    await pg_session.commit()
    repo = CatalogueReadRepository(pg_session)

    with_floor, _ = await gems(repo, cohort=50)
    without_floor, _ = await gems(repo, cohort=0)

    assert 10 in [row["mal_id"] for row in with_floor]
    # Ranked against the ten junk rows too, TV 10 is no longer in the top 20%.
    assert 10 not in [row["mal_id"] for row in without_floor]


async def test_hidden_gems_page_and_count(pg_session):
    await seed_gems(pg_session)
    repo = CatalogueReadRepository(pg_session)

    first, total = await gems(repo, limit=1, offset=0)
    second, _ = await gems(repo, limit=1, offset=1)
    beyond, beyond_total = await gems(repo, limit=1, offset=5)

    assert (first[0]["mal_id"], second[0]["mal_id"]) == (10, 101)
    assert total == 2
    assert beyond == [] and beyond_total == 2


async def test_an_empty_catalogue_has_no_gems(pg_session):
    rows, total = await gems(CatalogueReadRepository(pg_session))

    assert rows == [] and total == 0


# --- most dropped ----------------------------------------------------------------------


async def seed_dropped(session):
    # started = total - plan_to_watch, drop_rate = dropped / started.
    await add_anime(session, 1, members_mal=50_000)  # 0.5
    await add_list_stats(session, 1, total=40_000, plan_to_watch=10_000, dropped=15_000)
    await add_anime(session, 2, members_mal=50_000)  # 0.2
    await add_list_stats(session, 2, total=40_000, plan_to_watch=10_000, dropped=6_000)
    await add_anime(session, 3, members_mal=5_000)  # 0.9 but under the 10,000 floor
    await add_list_stats(session, 3, total=5_000, plan_to_watch=0, dropped=4_500)
    await add_anime(session, 4, members_mal=50_000, status=STATUS_NOT_YET_AIRED)
    await add_list_stats(session, 4, total=40_000, plan_to_watch=10_000, dropped=20_000)
    await add_anime(session, 5, members_mal=50_000)  # nobody started it
    await add_list_stats(session, 5, total=10_000, plan_to_watch=10_000, dropped=0)
    await add_anime(session, 6, type="Movie", members_mal=20_000)  # 0.3
    await add_list_stats(session, 6, total=20_000, plan_to_watch=0, dropped=6_000)
    await add_anime(session, 7, members_mal=50_000)  # no statistics row at all
    await add_anime(session, 8, members_mal=20_000, status=None)  # 0.5, ties with 1
    await add_list_stats(session, 8, total=20_000, plan_to_watch=0, dropped=10_000)
    await session.commit()


async def test_most_dropped_orders_by_drop_rate_then_members(pg_session):
    await seed_dropped(pg_session)

    rows, total = await CatalogueReadRepository(pg_session).most_dropped(NO_FILTERS, 10_000, 20, 0)

    assert [row["mal_id"] for row in rows] == [1, 8, 6, 2]  # 1 and 8 tie, more members first
    assert total == 4
    assert rows[0]["drop_rate"] == pytest.approx(0.5)
    assert (rows[0]["dropped"], rows[0]["started"]) == (15_000, 30_000)
    assert rows[2]["type"] == "Movie"


async def test_unaired_anime_and_anime_nobody_started_are_left_out(pg_session):
    await seed_dropped(pg_session)

    rows, _ = await CatalogueReadRepository(pg_session).most_dropped(NO_FILTERS, 0, 100, 0)

    ids = [row["mal_id"] for row in rows]
    assert 4 not in ids  # not yet aired
    assert 5 not in ids  # started is 0, no division by zero
    assert 7 not in ids  # no statistics row


async def test_the_members_floor_can_be_lowered(pg_session):
    await seed_dropped(pg_session)
    repo = CatalogueReadRepository(pg_session)

    default, _ = await repo.most_dropped(NO_FILTERS, 10_000, 20, 0)
    lowered, total = await repo.most_dropped(NO_FILTERS, 1_000, 20, 0)

    assert 3 not in [row["mal_id"] for row in default]
    assert lowered[0]["mal_id"] == 3  # 0.9, the highest rate, now qualifies
    assert total == 5


async def test_most_dropped_filters_page_and_count(pg_session):
    await seed_dropped(pg_session)
    repo = CatalogueReadRepository(pg_session)

    movies, movie_total = await repo.most_dropped(AggregateFilters(type="Movie"), 10_000, 20, 0)
    page, total = await repo.most_dropped(NO_FILTERS, 10_000, 2, 2)
    old, old_total = await repo.most_dropped(AggregateFilters(year_to=2000), 10_000, 20, 0)

    assert [row["mal_id"] for row in movies] == [6] and movie_total == 1
    assert [row["mal_id"] for row in page] == [6, 2] and total == 4
    assert old == [] and old_total == 0
