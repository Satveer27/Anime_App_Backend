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
from app.ingestion.parsers.details import clean_full
from app.ingestion.parsers.episodes import clean_episodes
from app.ingestion.parsers.reviews import clean_reviews
from app.ingestion.parsers.statistics import clean_statistics

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

    await repo.upsert_anime([Anime(mal_id=1, title="New", mal_score=Decimal("8.1"))])
    await repo.commit()

    anime = await get_anime(pg_session)
    assert anime.title == "New"
    assert anime.mal_score == Decimal("8.10")
    assert anime.imdb_score == Decimal("7.50")  # another pipeline's column is untouched
    assert anime.updated_at > before
    assert await count(pg_session, Anime) == 1


async def test_an_unchanged_anime_is_not_written_again(pg_session):
    repo = CatalogueRepository(pg_session)
    parsed = clean_full(
        {"data": {"mal_id": 1, "title": "T", "score": 8.75, "synopsis": "Story.", "year": 1998}}
    )
    assert await repo.upsert_anime([parsed]) == 1  # inserted
    await repo.commit()
    first = (await get_anime(pg_session)).updated_at

    same = clean_full(
        {"data": {"mal_id": 1, "title": "T", "score": 8.75, "synopsis": "Story.", "year": 1998}}
    )
    assert await repo.upsert_anime([same]) == 0  # nothing differs, so nothing is written
    await repo.commit()

    assert (await get_anime(pg_session)).updated_at == first


async def test_a_changed_anime_is_written_and_updated_at_moves(pg_session):
    repo = CatalogueRepository(pg_session)
    await repo.upsert_anime([Anime(mal_id=1, title="T", mal_score=Decimal("8.75"))])
    await repo.commit()
    first = (await get_anime(pg_session)).updated_at

    assert await repo.upsert_anime([Anime(mal_id=1, title="T", mal_score=Decimal("8.80"))]) == 1
    await repo.commit()

    anime = await get_anime(pg_session)
    assert anime.mal_score == Decimal("8.80")
    assert anime.updated_at > first


async def test_only_inserted_and_changed_anime_are_counted_in_a_batch(pg_session):
    repo = CatalogueRepository(pg_session)
    await repo.upsert_anime([Anime(mal_id=1, title="same"), Anime(mal_id=2, title="old")])
    await repo.commit()

    written = await repo.upsert_anime(
        [
            Anime(mal_id=1, title="same"),  # unchanged
            Anime(mal_id=2, title="new"),  # changed
            Anime(mal_id=3, title="fresh"),  # inserted
        ]
    )
    await repo.commit()

    assert written == 2
    assert (await get_anime(pg_session, 2)).title == "new"


async def test_null_compares_correctly_when_deciding_whether_a_row_changed(pg_session):
    repo = CatalogueRepository(pg_session)
    await repo.upsert_anime([Anime(mal_id=1, title="T", synopsis=None, year=2000)])
    await repo.commit()

    # NULL against NULL is no change; a value against NULL, and NULL against a value, are changes
    assert await repo.upsert_anime([Anime(mal_id=1, title="T", synopsis=None, year=2000)]) == 0
    assert await repo.upsert_anime([Anime(mal_id=1, title="T", synopsis="now", year=2000)]) == 1
    assert await repo.upsert_anime([Anime(mal_id=1, title="T", synopsis=None, year=2000)]) == 1
    await repo.commit()

    assert (await get_anime(pg_session)).synopsis is None


async def test_another_pipelines_columns_do_not_count_as_a_change(pg_session):
    await seed_anime(pg_session, 1, title="T", imdb_score=Decimal("9.1"))

    # imdb_score differs from nothing the parser sets, so only title/mal_score are compared
    written = await CatalogueRepository(pg_session).upsert_anime([Anime(mal_id=1, title="T")])

    assert written == 0
    assert (await get_anime(pg_session)).imdb_score == Decimal("9.10")


async def test_upsert_anime_inserts_a_new_row(pg_session):
    await CatalogueRepository(pg_session).upsert_anime([Anime(mal_id=7, title="Fresh", year=2024)])
    await pg_session.commit()

    anime = await get_anime(pg_session, 7)
    assert (anime.title, anime.year) == ("Fresh", 2024)
    assert anime.created_at is not None


async def test_upsert_anime_writes_null_when_the_source_dropped_a_value(pg_session):
    await seed_anime(pg_session, synopsis="text", synopsis_hash="abc")

    await CatalogueRepository(pg_session).upsert_anime(
        [Anime(mal_id=1, title="Title", synopsis=None, synopsis_hash=None)]
    )
    await pg_session.commit()

    anime = await get_anime(pg_session)
    assert anime.synopsis is None
    assert anime.synopsis_hash is None


async def test_upsert_anime_handles_a_whole_batch_in_chunks(pg_session):
    await seed_anime(pg_session, 1, title="old", imdb_score=Decimal("8.0"))
    total = ROWS_PER_STATEMENT + 100
    animes = [Anime(mal_id=n, title=f"title {n}") for n in range(1, total + 1)]

    written = await CatalogueRepository(pg_session).upsert_anime(animes)
    await pg_session.commit()

    assert written == total
    assert await count(pg_session, Anime) == total
    first = await get_anime(pg_session, 1)
    assert first.title == "title 1"
    assert first.imdb_score == Decimal("8.00")  # not set on the object, so untouched


async def test_upserting_no_anime_writes_nothing(pg_session):
    assert await CatalogueRepository(pg_session).upsert_anime([]) == 0


async def test_a_parsed_response_upserts_without_touching_other_pipelines_columns(pg_session):
    await seed_anime(
        pg_session, 1, title="old", imdb_score=Decimal("9.1"), anilist_score=Decimal("8.8")
    )
    parsed = clean_full(
        {"data": {"mal_id": 1, "title": "Cowboy Bebop", "score": 8.75, "synopsis": "Story."}}
    )

    await CatalogueRepository(pg_session).upsert_anime([parsed])
    await pg_session.commit()

    anime = await get_anime(pg_session)
    assert (anime.title, anime.mal_score) == ("Cowboy Bebop", Decimal("8.75"))
    assert anime.synopsis_hash == parsed.synopsis_hash
    assert anime.imdb_score == Decimal("9.10")
    assert anime.anilist_score == Decimal("8.80")


async def test_anime_ids_with_status_returns_the_matching_anime_only(pg_session):
    await seed_anime(pg_session, 1, status="Currently Airing")
    await seed_anime(pg_session, 2, status="Finished Airing")
    await seed_anime(pg_session, 3, status="Not yet aired")
    await seed_anime(pg_session, 4)  # no status stored
    repo = CatalogueRepository(pg_session)

    assert await repo.anime_ids_with_status(["Currently Airing", "Not yet aired"]) == {1, 3}
    assert await repo.anime_ids_with_status(("Finished Airing",)) == {2}
    assert await repo.anime_ids_with_status(["No such status"]) == set()


async def test_anime_ids_with_no_statuses_runs_no_query(pg_session):
    assert await CatalogueRepository(pg_session).anime_ids_with_status([]) == set()


async def test_existing_anime_ids_returns_only_the_ids_that_have_a_row(pg_session):
    await seed_anime(pg_session, 1)
    await seed_anime(pg_session, 3)
    repo = CatalogueRepository(pg_session)

    assert await repo.existing_anime_ids([1, 2, 3, 4]) == {1, 3}
    assert await repo.existing_anime_ids({2, 4}) == set()
    assert await repo.existing_anime_ids([1, 1, 1]) == {1}  # repeated ids are fine


async def test_existing_anime_ids_with_no_ids_runs_no_query(pg_session):
    assert await CatalogueRepository(pg_session).existing_anime_ids([]) == set()


async def test_existing_anime_ids_handles_a_whole_batch(pg_session):
    pg_session.add_all([Anime(mal_id=n, title=f"t{n}") for n in range(1, 501, 2)])  # odd ids
    await pg_session.commit()

    found = await CatalogueRepository(pg_session).existing_anime_ids(range(1, 501))

    assert found == set(range(1, 501, 2))


async def test_existing_anime_ids_sees_rows_upserted_in_the_same_transaction(pg_session):
    repo = CatalogueRepository(pg_session)
    await repo.upsert_anime([Anime(mal_id=5, title="new")])  # not committed yet

    assert await repo.existing_anime_ids([5, 6]) == {5}


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

    assert await repo.replace_anime_tags({1: [1, 2]}) == 1  # first write
    await repo.commit()
    assert await repo.replace_anime_tags({1: [2, 1, 1]}) == 0  # same set, any order
    assert await repo.replace_anime_tags({1: [2, 3]}) == 1  # changed
    await repo.commit()

    links = (await pg_session.execute(select(AnimeTag.tag_id))).scalars().all()
    assert sorted(links) == [2, 3]


async def test_replace_anime_tags_with_an_empty_list_clears_them(pg_session):
    await seed_anime(pg_session)
    repo = CatalogueRepository(pg_session)
    await repo.upsert_tags([(1, "Action")])
    await repo.replace_anime_tags({1: [1]})

    assert await repo.replace_anime_tags({1: []}) == 1
    await repo.commit()

    assert await count(pg_session, AnimeTag) == 0


async def test_an_anime_with_no_links_and_none_wanted_is_unchanged(pg_session):
    await seed_anime(pg_session)

    assert await CatalogueRepository(pg_session).replace_anime_tags({1: []}) == 0


async def test_links_of_several_anime_are_replaced_in_one_call_and_only_the_changed_count(
    pg_session,
):
    for anime_id in (1, 2, 3):
        await seed_anime(pg_session, anime_id)
    repo = CatalogueRepository(pg_session)
    await repo.upsert_tags([(1, "Action"), (2, "Comedy"), (3, "Drama")])
    await repo.replace_anime_tags({1: [1], 2: [1, 2], 3: [3]})
    await repo.commit()

    changed = await repo.replace_anime_tags(
        {
            1: [1],  # unchanged
            2: [2],  # lost a tag
            3: [3, 1],  # gained a tag
        }
    )
    await repo.commit()

    assert changed == 2
    rows = (await pg_session.execute(select(AnimeTag.anime_id, AnimeTag.tag_id))).all()
    assert sorted(tuple(r) for r in rows) == [(1, 1), (2, 2), (3, 1), (3, 3)]


async def test_replacing_some_animes_links_leaves_other_animes_alone(pg_session):
    await seed_anime(pg_session, 1)
    await seed_anime(pg_session, 2)
    repo = CatalogueRepository(pg_session)
    await repo.upsert_tags([(1, "Action"), (2, "Comedy")])
    await repo.replace_anime_tags({1: [1], 2: [1, 2]})

    await repo.replace_anime_tags({1: [2]})  # anime 2 is not mentioned
    await repo.commit()

    rows = (await pg_session.execute(select(AnimeTag.anime_id, AnimeTag.tag_id))).all()
    assert sorted(tuple(r) for r in rows) == [(1, 2), (2, 1), (2, 2)]


async def test_replacing_links_with_nothing_wanted_does_nothing(pg_session):
    assert await CatalogueRepository(pg_session).replace_anime_tags({}) == 0


async def test_a_large_batch_of_links_is_written_in_chunks(pg_session):
    anime_ids = range(1, 201)
    pg_session.add_all([Anime(mal_id=n, title=f"t{n}") for n in anime_ids])
    await pg_session.commit()
    repo = CatalogueRepository(pg_session)
    await repo.upsert_tags([(t, f"tag {t}") for t in range(1, 11)])

    changed = await repo.replace_anime_tags({n: range(1, 11) for n in anime_ids})  # 2,000 links
    await repo.commit()

    assert changed == 200
    assert await count(pg_session, AnimeTag) == 2000


async def test_replace_anime_studios_writes_only_when_the_set_changes(pg_session):
    await seed_anime(pg_session)
    repo = CatalogueRepository(pg_session)
    await repo.upsert_studios([(10, "Madhouse"), (11, "Bones")])

    assert await repo.replace_anime_studios({1: [10]}) == 1
    assert await repo.replace_anime_studios({1: [10]}) == 0
    assert await repo.replace_anime_studios({1: [10, 11]}) == 1
    await repo.commit()

    assert await count(pg_session, AnimeStudio) == 2


async def test_replace_relations_needs_no_row_for_the_target_anime(pg_session):
    await seed_anime(pg_session)
    repo = CatalogueRepository(pg_session)

    assert await repo.replace_relations({1: [("Sequel", 500), ("Prequel", 400)]}) == 1
    await repo.commit()
    assert await repo.replace_relations({1: [("Prequel", 400), ("Sequel", 500)]}) == 0
    assert await repo.replace_relations({1: [("Sequel", 500)]}) == 1
    await repo.commit()

    rows = (await pg_session.execute(select(AnimeRelation))).scalars().all()
    assert [(r.relation, r.related_mal_id) for r in rows] == [("Sequel", 500)]


async def test_links_for_an_unknown_anime_are_rejected(pg_session):
    repo = CatalogueRepository(pg_session)
    await repo.upsert_tags([(1, "Action")])

    with pytest.raises(IntegrityError):
        await repo.replace_anime_tags({99: [1]})


# --- statistics, episodes, reviews -------------------------------------------------------------


def stats(**overrides) -> AnimeStatisticsMal:
    values = {
        "anime_id": 1,
        "watching": 10,
        "completed": 20,
        "on_hold": 1,
        "dropped": 2,
        "plan_to_watch": 30,
        "total": 63,
        "votes_10": 5,
    }
    values.update(overrides)
    return AnimeStatisticsMal(**values)


async def test_statistics_are_overwritten_on_refresh(pg_session):
    await seed_anime(pg_session)
    repo = CatalogueRepository(pg_session)
    await repo.upsert_statistics([stats()])
    await repo.commit()
    first = (await pg_session.execute(select(AnimeStatisticsMal))).scalar_one().updated_at

    await repo.upsert_statistics([stats(watching=99, total=152)])
    await repo.commit()
    pg_session.expire_all()

    row = (await pg_session.execute(select(AnimeStatisticsMal))).scalar_one()
    assert (row.watching, row.total, row.votes_10) == (99, 152, 5)
    assert row.updated_at > first
    assert await count(pg_session, AnimeStatisticsMal) == 1


async def test_statistics_of_several_anime_go_in_one_call(pg_session):
    for anime_id in (1, 2, 3):
        await seed_anime(pg_session, anime_id)

    written = await CatalogueRepository(pg_session).upsert_statistics(
        [stats(anime_id=anime_id, total=anime_id * 100) for anime_id in (1, 2, 3)]
    )
    await pg_session.commit()

    assert written == 3
    totals = (
        (
            await pg_session.execute(
                select(AnimeStatisticsMal.total).order_by(AnimeStatisticsMal.anime_id)
            )
        )
        .scalars()
        .all()
    )
    assert totals == [100, 200, 300]


async def test_episodes_upsert_in_chunks_and_leave_other_sources_scores_alone(pg_session):
    await seed_anime(pg_session)
    pg_session.add(Episode(anime_id=1, episode_number=1, title="old", imdb_score=Decimal("9.0")))
    await pg_session.commit()
    total = ROWS_PER_STATEMENT * 2 + 50
    rows = [
        Episode(anime_id=1, episode_number=n, title=f"ep {n}", mal_score=None)
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
    assert await repo.upsert_statistics([]) == 0


def review(**overrides) -> Review:
    values = {
        "mal_id": 900,
        "anime_id": 1,
        "review": "great",
        "username": "someone",
        "reaction_overall": 10,
    }
    values.update(overrides)
    return Review(**values)


async def test_reviews_upsert_on_their_own_id_and_refresh_the_counts(pg_session):
    await seed_anime(pg_session)
    repo = CatalogueRepository(pg_session)
    await repo.upsert_reviews([review(), review(mal_id=901, reaction_overall=3)])
    await repo.commit()
    first = (await pg_session.execute(select(Review.updated_at))).scalars().first()

    await repo.upsert_reviews([review(reaction_overall=25)])
    await repo.commit()
    pg_session.expire_all()

    assert await count(pg_session, Review) == 2
    updated = (await pg_session.execute(select(Review).where(Review.mal_id == 900))).scalar_one()
    assert updated.reaction_overall == 25
    assert updated.username == "someone"
    assert updated.updated_at > first  # means "last seen", so it moves even with equal data


async def test_a_review_that_left_page_one_is_kept(pg_session):
    await seed_anime(pg_session)
    repo = CatalogueRepository(pg_session)
    await repo.upsert_reviews([review(mal_id=1), review(mal_id=2)])
    await repo.commit()

    await repo.upsert_reviews([review(mal_id=2), review(mal_id=3)])  # review 1 fell off page 1
    await repo.commit()

    assert await count(pg_session, Review) == 3


async def test_children_of_a_missing_anime_are_rejected(pg_session):
    with pytest.raises(IntegrityError):
        await CatalogueRepository(pg_session).upsert_episodes(
            [Episode(anime_id=99, episode_number=1, title="x")]
        )


async def test_parsed_episodes_upsert_cleanly_and_refresh_in_place(pg_session):
    await seed_anime(pg_session)
    pg_session.add(Episode(anime_id=1, episode_number=1, title="old", imdb_score=Decimal("9.0")))
    await pg_session.commit()
    page = {
        "data": [
            {"mal_id": 0, "title": "Special"},  # no flags, no score: NOT NULL flags must default
            {
                "mal_id": 1,
                "title": "Asteroid Blues",
                "score": 4.32,
                "filler": True,
                "duration": 1440,
            },
            {"mal_id": 2, "title": "Stray Dog Strut", "score": 0},
        ]
    }
    repo = CatalogueRepository(pg_session)

    await repo.upsert_episodes(clean_episodes([page], anime_id=1))
    await repo.commit()
    await repo.upsert_episodes(clean_episodes([page], anime_id=1))  # the same data again
    await repo.commit()
    pg_session.expire_all()

    assert await count(pg_session, Episode) == 3
    rows = {
        e.episode_number: e for e in (await pg_session.execute(select(Episode))).scalars().all()
    }
    assert (rows[0].filler, rows[0].recap) == (False, False)
    assert rows[1].title == "Asteroid Blues"
    assert (rows[1].mal_score, rows[1].filler, rows[1].duration_seconds) == (
        Decimal("8.64"),
        True,
        1440,
    )
    assert rows[1].imdb_score == Decimal("9.00")  # another pipeline's column survives
    assert rows[2].mal_score is None  # MAL's 0 means no score


async def get_episode(session, number=1) -> Episode:
    session.expire_all()
    return (
        await session.execute(select(Episode).where(Episode.episode_number == number))
    ).scalar_one()


async def test_a_mal_episode_without_a_synopsis_keeps_the_stored_one(pg_session):
    await seed_anime(pg_session)
    pg_session.add(
        Episode(anime_id=1, episode_number=1, title="old", synopsis="from IMDb", synopsis_hash="h1")
    )
    await pg_session.commit()
    repo = CatalogueRepository(pg_session)

    await repo.upsert_episodes(
        [
            Episode(
                anime_id=1, episode_number=1, title="MAL title", synopsis=None, synopsis_hash=None
            )
        ]
    )
    await repo.commit()

    episode = await get_episode(pg_session)
    assert episode.title == "MAL title"  # other columns still follow MAL
    assert (episode.synopsis, episode.synopsis_hash) == ("from IMDb", "h1")


async def test_a_mal_synopsis_overwrites_the_stored_one_and_its_hash(pg_session):
    await seed_anime(pg_session)
    pg_session.add(
        Episode(anime_id=1, episode_number=1, synopsis="from IMDb", synopsis_hash="imdb-hash")
    )
    await pg_session.commit()

    await CatalogueRepository(pg_session).upsert_episodes(
        [Episode(anime_id=1, episode_number=1, synopsis="from MAL", synopsis_hash="mal-hash")]
    )
    await pg_session.commit()

    episode = await get_episode(pg_session)
    assert (episode.synopsis, episode.synopsis_hash) == ("from MAL", "mal-hash")


async def test_a_new_mal_episode_with_no_synopsis_is_stored_without_one(pg_session):
    await seed_anime(pg_session)

    await CatalogueRepository(pg_session).upsert_episodes(
        [Episode(anime_id=1, episode_number=1, title="t", synopsis=None, synopsis_hash=None)]
    )
    await pg_session.commit()

    episode = await get_episode(pg_session)
    assert (episode.synopsis, episode.synopsis_hash) == (None, None)


async def test_only_the_synopsis_columns_are_protected_from_a_null(pg_session):
    await seed_anime(pg_session)
    pg_session.add(Episode(anime_id=1, episode_number=1, title="old title", synopsis="kept"))
    await pg_session.commit()

    await CatalogueRepository(pg_session).upsert_episodes(
        [Episode(anime_id=1, episode_number=1, title=None, synopsis=None, synopsis_hash=None)]
    )
    await pg_session.commit()

    episode = await get_episode(pg_session)
    assert episode.title is None  # a NULL title still mirrors MAL
    assert episode.synopsis == "kept"


async def test_episodes_of_several_anime_go_in_one_call(pg_session):
    for anime_id in (1, 2, 3):
        await seed_anime(pg_session, anime_id)
    per_anime = 400  # 1,200 rows in all, so the call crosses chunk boundaries
    episodes = [
        Episode(anime_id=anime_id, episode_number=n, title=f"{anime_id}-{n}")
        for anime_id in (1, 2, 3)
        for n in range(1, per_anime + 1)
    ]

    written = await CatalogueRepository(pg_session).upsert_episodes(episodes)
    await pg_session.commit()

    assert written == 3 * per_anime
    for anime_id in (1, 2, 3):
        rows = (
            await pg_session.execute(select(func.count()).where(Episode.anime_id == anime_id))
        ).scalar_one()
        assert rows == per_anime
    one = (
        await pg_session.execute(
            select(Episode).where(Episode.anime_id == 2, Episode.episode_number == 7)
        )
    ).scalar_one()
    assert one.title == "2-7"


async def test_parsed_statistics_and_reviews_upsert_and_refresh_in_place(pg_session):
    await seed_anime(pg_session)
    repo = CatalogueRepository(pg_session)
    stats_payload = {
        "data": {
            "watching": 1,
            "completed": 2,
            "on_hold": 3,
            "dropped": 4,
            "plan_to_watch": 5,
            "total": 15,
            "scores": [{"score": 10, "votes": 7}],
        }
    }
    reviews_payload = {
        "data": [
            {
                "mal_id": 5,
                "review": "Ed&rsquo;s a girl",
                "score": 9,
                "user": {"username": "someone"},
                "reactions": {"overall": 3},
            },
            {"mal_id": 6, "review": "second"},  # no flags, user or reactions
        ]
    }

    for _ in range(2):  # the same data twice must not duplicate or fail
        await repo.upsert_statistics([clean_statistics(stats_payload, 1)])
        await repo.upsert_reviews(clean_reviews(reviews_payload, 1))
        await repo.commit()
    pg_session.expire_all()

    row = (await pg_session.execute(select(AnimeStatisticsMal))).scalar_one()
    assert (row.watching, row.total, row.votes_10, row.votes_1) == (1, 15, 7, None)
    reviews = {r.mal_id: r for r in (await pg_session.execute(select(Review))).scalars().all()}
    assert len(reviews) == 2
    assert reviews[5].review == "Ed\u2019s a girl"
    assert (reviews[5].username, reviews[5].reaction_overall, reviews[5].reaction_nice) == (
        "someone",
        3,
        None,
    )
    assert (reviews[6].is_spoiler, reviews[6].is_preliminary) == (False, False)
