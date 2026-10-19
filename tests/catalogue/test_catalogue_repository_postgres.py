from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.catalogue.models import (
    Anime,
    AnimeRelation,
    AnimeStatisticsMal,
    AnimeStudio,
    AnimeTag,
    Episode,
    Review,
    Studio,
    Tag,
)
from app.catalogue.repository import ROWS_PER_STATEMENT, CatalogueRepository

pytestmark = pytest.mark.postgres


async def seed_anime(session, mal_id=1, **fields):
    session.add(Anime(mal_id=mal_id, title=fields.pop("title", "Title"), **fields))
    await session.commit()


async def get_anime(session, mal_id=1) -> Anime:
    session.expire_all()
    return (await session.execute(select(Anime).where(Anime.mal_id == mal_id))).scalar_one()


async def count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# --- anime -------------------------------------------------------------------------------


async def test_upsert_anime_inserts_then_updates_only_the_given_columns(pg_session):
    await seed_anime(pg_session, title="Old", imdb_score=Decimal("7.5"))
    before = (await get_anime(pg_session)).updated_at
    repo = CatalogueRepository(pg_session)

    await repo.upsert_anime({"mal_id": 1, "title": "New", "mal_score": Decimal("8.1")})
    await repo.commit()

    anime = await get_anime(pg_session)
    assert anime.title == "New"
    assert anime.mal_score == Decimal("8.10")
    assert anime.imdb_score == Decimal("7.50")  # another pipeline's column is untouched
    assert anime.updated_at > before
    assert await count(pg_session, Anime) == 1


async def test_upsert_anime_inserts_a_new_row(pg_session):
    await CatalogueRepository(pg_session).upsert_anime(
        {"mal_id": 7, "title": "Fresh", "year": 2024}
    )
    await pg_session.commit()

    anime = await get_anime(pg_session, 7)
    assert (anime.title, anime.year) == ("Fresh", 2024)
    assert anime.created_at is not None


async def test_upsert_anime_writes_null_when_the_source_dropped_a_value(pg_session):
    await seed_anime(pg_session, synopsis="text", synopsis_hash="abc")

    await CatalogueRepository(pg_session).upsert_anime(
        {"mal_id": 1, "title": "Title", "synopsis": None, "synopsis_hash": None}
    )
    await pg_session.commit()

    anime = await get_anime(pg_session)
    assert anime.synopsis is None
    assert anime.synopsis_hash is None


async def test_anime_exists_and_synopsis_hash(pg_session):
    repo = CatalogueRepository(pg_session)
    assert await repo.anime_exists(1) is False
    assert await repo.get_synopsis_hash(1) is None

    await seed_anime(pg_session, synopsis_hash="h1")

    assert await repo.anime_exists(1) is True
    assert await repo.get_synopsis_hash(1) == "h1"


# --- tags and studios ------------------------------------------------------------------------


async def test_tags_are_loaded_as_a_dict_and_renamed_in_place(pg_session):
    repo = CatalogueRepository(pg_session)
    assert await repo.get_tags() == {}

    await repo.upsert_tags([(1, "Action"), (2, "Comedy")])
    await repo.commit()
    await repo.upsert_tags([(2, "Comedy!"), (3, "Drama")])  # 2 renamed, 3 new
    await repo.commit()

    assert await repo.get_tags() == {1: "Action", 2: "Comedy!", 3: "Drama"}
    assert await count(pg_session, Tag) == 3


async def test_studios_are_loaded_as_a_dict_and_renamed_in_place(pg_session):
    repo = CatalogueRepository(pg_session)

    await repo.upsert_studios([(10, "Madhouse")])
    await repo.upsert_studios([(10, "MADHOUSE"), (11, "Bones")])
    await repo.commit()

    assert await repo.get_studios() == {10: "MADHOUSE", 11: "Bones"}
    assert await count(pg_session, Studio) == 2


# --- link tables -----------------------------------------------------------------------------


async def test_replace_anime_tags_writes_only_when_the_set_changes(pg_session):
    await seed_anime(pg_session)
    repo = CatalogueRepository(pg_session)
    await repo.upsert_tags([(1, "Action"), (2, "Comedy"), (3, "Drama")])

    assert await repo.replace_anime_tags(1, [1, 2]) is True  # first write
    await repo.commit()
    assert await repo.replace_anime_tags(1, [2, 1, 1]) is False  # same set, any order
    assert await repo.replace_anime_tags(1, [2, 3]) is True  # changed
    await repo.commit()

    links = (await pg_session.execute(select(AnimeTag.tag_id))).scalars().all()
    assert sorted(links) == [2, 3]


async def test_replace_anime_tags_with_an_empty_list_clears_them(pg_session):
    await seed_anime(pg_session)
    repo = CatalogueRepository(pg_session)
    await repo.upsert_tags([(1, "Action")])
    await repo.replace_anime_tags(1, [1])

    assert await repo.replace_anime_tags(1, []) is True
    await repo.commit()

    assert await count(pg_session, AnimeTag) == 0


async def test_replacing_one_animes_links_leaves_other_animes_alone(pg_session):
    await seed_anime(pg_session, 1)
    await seed_anime(pg_session, 2)
    repo = CatalogueRepository(pg_session)
    await repo.upsert_tags([(1, "Action"), (2, "Comedy")])
    await repo.replace_anime_tags(1, [1])
    await repo.replace_anime_tags(2, [1, 2])

    await repo.replace_anime_tags(1, [2])
    await repo.commit()

    rows = (await pg_session.execute(select(AnimeTag.anime_id, AnimeTag.tag_id))).all()
    assert sorted(tuple(r) for r in rows) == [(1, 2), (2, 1), (2, 2)]


async def test_replace_anime_studios_writes_only_when_the_set_changes(pg_session):
    await seed_anime(pg_session)
    repo = CatalogueRepository(pg_session)
    await repo.upsert_studios([(10, "Madhouse"), (11, "Bones")])

    assert await repo.replace_anime_studios(1, [10]) is True
    assert await repo.replace_anime_studios(1, [10]) is False
    assert await repo.replace_anime_studios(1, [10, 11]) is True
    await repo.commit()

    assert await count(pg_session, AnimeStudio) == 2


async def test_replace_relations_needs_no_row_for_the_target_anime(pg_session):
    await seed_anime(pg_session)
    repo = CatalogueRepository(pg_session)

    assert await repo.replace_relations(1, [("Sequel", 500), ("Prequel", 400)]) is True
    await repo.commit()
    assert await repo.replace_relations(1, [("Prequel", 400), ("Sequel", 500)]) is False
    assert await repo.replace_relations(1, [("Sequel", 500)]) is True
    await repo.commit()

    rows = (await pg_session.execute(select(AnimeRelation))).scalars().all()
    assert [(r.relation, r.related_mal_id) for r in rows] == [("Sequel", 500)]


async def test_links_for_an_unknown_anime_are_rejected(pg_session):
    repo = CatalogueRepository(pg_session)
    await repo.upsert_tags([(1, "Action")])

    with pytest.raises(IntegrityError):
        await repo.replace_anime_tags(99, [1])


# --- statistics, episodes, reviews -------------------------------------------------------------


STATS = {
    "anime_id": 1,
    "watching": 10,
    "completed": 20,
    "on_hold": 1,
    "dropped": 2,
    "plan_to_watch": 30,
    "total": 63,
    "votes_10": 5,
}


async def test_statistics_are_overwritten_on_refresh(pg_session):
    await seed_anime(pg_session)
    repo = CatalogueRepository(pg_session)
    await repo.upsert_statistics(STATS)
    await repo.commit()
    first = (await pg_session.execute(select(AnimeStatisticsMal))).scalar_one().updated_at

    await repo.upsert_statistics({**STATS, "watching": 99, "total": 152})
    await repo.commit()
    pg_session.expire_all()

    row = (await pg_session.execute(select(AnimeStatisticsMal))).scalar_one()
    assert (row.watching, row.total, row.votes_10) == (99, 152, 5)
    assert row.updated_at > first
    assert await count(pg_session, AnimeStatisticsMal) == 1


async def test_episodes_upsert_in_chunks_and_leave_other_sources_scores_alone(pg_session):
    await seed_anime(pg_session)
    pg_session.add(Episode(anime_id=1, episode_number=1, title="old", imdb_score=Decimal("9.0")))
    await pg_session.commit()
    total = ROWS_PER_STATEMENT * 2 + 50
    rows = [
        {"anime_id": 1, "episode_number": n, "title": f"ep {n}", "mal_score": None}
        for n in range(1, total + 1)
    ]
    repo = CatalogueRepository(pg_session)

    written = await repo.upsert_episodes(rows)
    await repo.commit()

    assert written == total
    assert await count(pg_session, Episode) == total
    first = (
        await pg_session.execute(select(Episode).where(Episode.episode_number == 1))
    ).scalar_one()
    assert first.title == "ep 1"
    assert first.imdb_score == Decimal("9.00")  # not in the rows, so untouched
    assert first.mal_score is None


async def test_upserting_no_rows_writes_nothing(pg_session):
    repo = CatalogueRepository(pg_session)

    assert await repo.upsert_episodes([]) == 0
    assert await repo.upsert_reviews([]) == 0


async def test_reviews_upsert_on_their_own_id_and_refresh_the_counts(pg_session):
    await seed_anime(pg_session)
    repo = CatalogueRepository(pg_session)
    review = {
        "mal_id": 900,
        "anime_id": 1,
        "review": "great",
        "username": "someone",
        "reaction_overall": 10,
    }
    await repo.upsert_reviews([review, {**review, "mal_id": 901, "reaction_overall": 3}])
    await repo.commit()
    first = (await pg_session.execute(select(Review.updated_at))).scalars().first()

    await repo.upsert_reviews([{**review, "reaction_overall": 25}])
    await repo.commit()
    pg_session.expire_all()

    assert await count(pg_session, Review) == 2
    updated = (await pg_session.execute(select(Review).where(Review.mal_id == 900))).scalar_one()
    assert updated.reaction_overall == 25
    assert updated.username == "someone"
    assert updated.updated_at >= first


async def test_children_of_a_missing_anime_are_rejected(pg_session):
    with pytest.raises(IntegrityError):
        await CatalogueRepository(pg_session).upsert_episodes(
            [{"anime_id": 99, "episode_number": 1, "title": "x"}]
        )
