import importlib.util
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

from app.catalogue.models import SEARCH_TEXT_SQL, Anime
from app.catalogue.read_repository import (
    AnimeFilters,
    CatalogueReadRepository,
    _escape_like,
    is_statement_timeout,
)


class _PgError(Exception):
    def __init__(self, sqlstate=None, pgcode=None):
        super().__init__("pg error")
        if sqlstate is not None:
            self.sqlstate = sqlstate
        if pgcode is not None:
            self.pgcode = pgcode


def sql(clause) -> str:
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


class TestEscapeLike:
    def test_wildcards_become_literals(self):
        assert _escape_like("100%") == "100\\%"
        assert _escape_like("a_b") == "a\\_b"

    def test_the_escape_character_is_escaped_first(self):
        assert _escape_like("a\\b") == "a\\\\b"
        assert _escape_like("\\%") == "\\\\\\%"

    def test_plain_text_is_unchanged(self):
        assert _escape_like("attack on titan") == "attack on titan"


class TestIsStatementTimeout:
    def test_the_postgres_cancel_code_is_a_timeout(self):
        assert is_statement_timeout(DBAPIError("S", {}, _PgError(sqlstate="57014")))
        assert is_statement_timeout(DBAPIError("S", {}, _PgError(pgcode="57014")))

    def test_other_codes_are_not(self):
        assert not is_statement_timeout(DBAPIError("S", {}, _PgError(sqlstate="42P01")))
        assert not is_statement_timeout(DBAPIError("S", {}, _PgError()))


class TestSearchIndex:
    """The search only uses its trigram index while the query and the index share one expression."""

    def test_the_migration_that_builds_the_index_uses_the_same_expression_as_the_query(self):
        path = next(Path("alembic/versions").glob("d4a9c2e7f1b8_*.py"))
        spec = importlib.util.spec_from_file_location("trigram_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        assert migration.SEARCH_TEXT_SQL == SEARCH_TEXT_SQL

    def test_the_query_text_is_the_index_expression(self):
        (condition,) = CatalogueReadRepository(db=None)._anime_conditions(AnimeFilters(q="x"))

        assert SEARCH_TEXT_SQL in str(condition.compile(dialect=postgresql.dialect()))

    def test_the_model_declares_the_search_and_sort_indexes(self):
        names = {index.name for index in Anime.__table__.indexes}

        assert {"ix_anime_search_trgm", "ix_anime_members_mal", "ix_anime_mal_score"} <= names


class TestSearchSql:
    """Checks on the SQL text, without a database."""

    repository = CatalogueReadRepository(db=None)

    def test_no_filters_means_no_conditions(self):
        assert self.repository._anime_conditions(AnimeFilters()) == []

    def test_every_filter_adds_one_condition(self):
        filters = AnimeFilters(
            q="titan", types=("TV",), statuses=("Currently Airing",), seasons=("fall",),
            year=2020, year_from=2010, year_to=2019, tag_ids=(1,), studio_ids=(2,),
            min_score=7.0, min_ratings=500,
        )  # fmt: skip

        assert len(self.repository._anime_conditions(filters)) == 11

    def test_several_values_become_an_in_list(self):
        conditions = self.repository._anime_conditions(
            AnimeFilters(
                types=("Movie", "TV"), statuses=("Currently Airing", "Not yet aired"),
                seasons=("fall", "winter"), studio_ids=(1, 2),
            )
        )  # fmt: skip

        texts = [sql(c) for c in conditions]
        assert "anime.type IN ('Movie', 'TV')" in texts[0]
        assert "anime.status IN ('Currently Airing', 'Not yet aired')" in texts[1]
        assert "anime.season IN ('fall', 'winter')" in texts[2]
        assert "studio_id IN (1, 2)" in texts[3]

    def test_tags_must_all_match_by_counting_them(self):
        (condition,) = self.repository._anime_conditions(AnimeFilters(tag_ids=(1, 4)))
        text = sql(condition)

        assert "tag_id IN (1, 4)" in text
        assert "GROUP BY anime_tag.anime_id" in text
        assert "count(*) = 2" in text

    def test_the_same_tag_twice_is_still_one_tag(self):
        (condition,) = self.repository._anime_conditions(AnimeFilters(tag_ids=(1, 1)))

        assert "count(*) = 1" in sql(condition)

    def test_tags_can_match_any(self):
        (condition,) = self.repository._anime_conditions(
            AnimeFilters(tag_ids=(1, 4), tag_match_all=False)
        )
        text = sql(condition)

        assert "tag_id IN (1, 4)" in text
        assert "GROUP BY" not in text
        assert "count(" not in text

    def test_a_single_tag_needs_no_counting_to_be_correct(self):
        (condition,) = self.repository._anime_conditions(AnimeFilters(tag_ids=(7,)))

        assert "tag_id IN (7)" in sql(condition)
