import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.catalogue.models import Anime
from app.ingestion.enums.match_method_enum import MatchMethod
from app.ingestion.enums.match_status_enum import MatchStatus
from app.ingestion.enums.snapshot_status_enum import SnapshotStatus
from app.ingestion.models import AnimeImdbMatch, ImdbSnapshot

pytestmark = pytest.mark.postgres


async def count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def add_anime(session, mal_id: int = 1) -> None:
    session.add(Anime(mal_id=mal_id, title=f"Anime {mal_id}"))
    await session.flush()


def match(anime_id: int = 1, **overrides) -> AnimeImdbMatch:
    values = {
        "anime_id": anime_id,
        "method": MatchMethod.TITLE,
        "status": MatchStatus.ACCEPTED,
        **overrides,
    }
    return AnimeImdbMatch(**values)


# Raw SQL, so the database CHECK is what rejects the value and not the ORM enum type.
async def test_match_method_outside_the_allowed_values_is_rejected(pg_session):
    await add_anime(pg_session)
    with pytest.raises(IntegrityError, match="ck_anime_imdb_match_method"):
        await pg_session.execute(
            text(
                "INSERT INTO anime_imdb_match (anime_id, method, status) "
                "VALUES (1, 'guess', 'accepted')"
            )
        )


async def test_match_status_outside_the_allowed_values_is_rejected(pg_session):
    await add_anime(pg_session)
    with pytest.raises(IntegrityError, match="ck_anime_imdb_match_status"):
        await pg_session.execute(
            text(
                "INSERT INTO anime_imdb_match (anime_id, method, status) "
                "VALUES (1, 'title', 'maybe')"
            )
        )


async def test_snapshot_status_outside_the_allowed_values_is_rejected(pg_session):
    with pytest.raises(IntegrityError, match="ck_imdb_snapshot_status"):
        await pg_session.execute(text("INSERT INTO imdb_snapshot (status) VALUES ('loaded')"))


async def test_snapshot_defaults_to_running_with_no_etags(pg_session):
    pg_session.add(ImdbSnapshot())
    await pg_session.flush()

    snapshot = (await pg_session.execute(select(ImdbSnapshot))).scalar_one()

    assert snapshot.id == 1
    assert snapshot.status is SnapshotStatus.RUNNING
    assert snapshot.basics_etag is None
    assert snapshot.finished_at is None


async def test_deleting_an_anime_deletes_its_match_row(pg_session):
    await add_anime(pg_session, 1)
    await add_anime(pg_session, 2)
    pg_session.add_all([match(1), match(2)])
    await pg_session.flush()

    await pg_session.execute(text("DELETE FROM anime WHERE mal_id = 1"))

    remaining = (await pg_session.execute(select(AnimeImdbMatch.anime_id))).scalars().all()
    assert remaining == [2]


async def test_match_accepts_a_null_imdb_tconst_and_an_unknown_tconst(pg_session):
    await add_anime(pg_session, 1)
    await add_anime(pg_session, 2)
    # imdb_tconst references no table: the IMDb data is not stored (ADR 0019).
    pg_session.add_all(
        [
            match(1, imdb_tconst=None, status=MatchStatus.UNMATCHED),
            match(2, imdb_tconst="tt-not-in-imdb-title"),
        ]
    )
    await pg_session.flush()

    assert await count(pg_session, AnimeImdbMatch) == 2


async def test_a_second_match_row_for_the_same_anime_is_rejected(pg_session):
    await add_anime(pg_session)
    pg_session.add(match(1))
    await pg_session.flush()

    pg_session.add(match(1, method=MatchMethod.OVERRIDE))
    with pytest.raises(IntegrityError):
        await pg_session.flush()
