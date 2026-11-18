import importlib.util
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

from app.catalogue.enums import AnimeSort, Order, ReleaseStatus
from app.catalogue.models import SEARCH_TEXT_SQL, Anime, Episode
from app.catalogue.read_repository import (
    RELEASING_WITHIN_DAYS,
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
        assert "ix_episode_aired" in {index.name for index in Episode.__table__.indexes}


class TestSearchSql:
    """Checks on the SQL text, without a database."""

    repository = CatalogueReadRepository(db=None)

    def test_no_filters_means_no_conditions(self):
        assert self.repository._anime_conditions(AnimeFilters()) == []

    def test_every_filter_adds_one_condition(self):
        filters = AnimeFilters(
            q="titan", types=("TV",), statuses=("Currently Airing",), seasons=("fall",),
            year=2020, year_from=2010, year_to=2019, tag_ids=(1,), studio_ids=(2,),
            release_status=ReleaseStatus.RELEASING, released_since=datetime(2026, 11, 16, tzinfo=UTC),
            min_score=7.0, min_ratings=500,
        )  # fmt: skip

        assert len(self.repository._anime_conditions(filters)) == 13

    def test_released_since_is_an_episode_released_on_or_after_that_moment(self):
        since = datetime(2026, 11, 16, tzinfo=UTC)

        (condition,) = self.repository._anime_conditions(AnimeFilters(released_since=since))
        compiled = condition.compile(dialect=postgresql.dialect())
        text = str(compiled)

        assert "EXISTS" in text
        assert "episode.anime_id = anime.mal_id" in text
        assert "episode.aired <= now()" in text  # a future-dated episode is not a release
        assert "episode.aired >= " in text
        assert since in compiled.params.values()

    def test_released_since_is_a_bound_parameter(self):
        since = datetime(2026, 11, 16, tzinfo=UTC)

        (condition,) = self.repository._anime_conditions(AnimeFilters(released_since=since))

        assert "2026" not in str(condition.compile(dialect=postgresql.dialect()))

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


class TestReleaseSql:
    repository = CatalogueReadRepository(db=None)

    def condition(self, release_status):
        return sql(self.repository._release_condition(release_status))

    def test_releasing_is_an_episode_aired_in_the_last_ten_days(self):
        text = self.condition(ReleaseStatus.RELEASING)

        assert "EXISTS" in text
        assert "episode.anime_id = anime.mal_id" in text
        assert "episode.aired <= now()" in text
        assert "episode.aired >= now() - " in text
        assert RELEASING_WITHIN_DAYS == 10

    def test_a_future_dated_episode_is_a_schedule_not_a_release(self):
        for status in ReleaseStatus:
            if status is not ReleaseStatus.NO_EPISODE_DATA:
                assert "episode.aired <= now()" in self.condition(status)

    def test_on_break_is_airing_with_released_episodes_but_none_recently(self):
        text = self.condition(ReleaseStatus.ON_BREAK)

        assert "anime.status = 'Currently Airing'" in text
        assert text.count("EXISTS") == 2
        assert "NOT (EXISTS" in text

    def test_no_episode_data_is_airing_with_no_episode_rows_at_all(self):
        text = self.condition(ReleaseStatus.NO_EPISODE_DATA)

        assert "anime.status = 'Currently Airing'" in text
        assert "NOT (EXISTS" in text
        assert "episode.aired" not in text  # any episode at all, dated or not

    def test_sorting_by_the_latest_episode_uses_the_last_released_date(self):
        order = self.repository._anime_order(AnimeSort.LATEST_EPISODE, None, None)
        text = sql(order[0])

        assert "max(episode.aired)" in text
        assert "episode.aired <= now()" in text
        assert "DESC NULLS LAST" in text
        assert "mal_id ASC" in sql(order[-1])

    def test_search_matches_the_start_of_any_word_in_any_of_the_three_titles(self):
        (condition,) = self.repository._anime_conditions(AnimeFilters(q="titan"))
        compiled = condition.compile(dialect=postgresql.dialect())

        # one ILIKE on the combined title text: a space and the text, anywhere (the start of a
        # word, since every title sits behind a space)
        assert list(compiled.params.values()) == ["% titan%"]
        for column in ("title", "title_english", "title_japanese"):
            assert column in str(compiled)

    def test_search_text_cannot_act_as_a_pattern(self):
        (condition,) = self.repository._anime_conditions(AnimeFilters(q="100%_x"))
        compiled = condition.compile(dialect=postgresql.dialect())

        # the user's % and _ are escaped; only the trailing % is the wildcard we add
        assert list(compiled.params.values()) == ["% 100\\%\\_x%"]
        assert "ESCAPE" in str(compiled)

    def test_search_text_is_a_bound_parameter_never_part_of_the_sql(self):
        (condition,) = self.repository._anime_conditions(AnimeFilters(q="'; DROP TABLE anime;--"))
        compiled = condition.compile(dialect=postgresql.dialect())

        assert "DROP TABLE" not in str(compiled)
        assert any("DROP TABLE" in value for value in compiled.params.values())

    def test_every_order_ends_with_the_id_so_pages_are_stable(self):
        for sort in (
            AnimeSort.MEMBERS,
            AnimeSort.SCORE,
            AnimeSort.FAVORITES,
            AnimeSort.NEWEST,
            AnimeSort.TITLE,
        ):
            order = self.repository._anime_order(sort, None, None)
            assert "mal_id ASC" in sql(order[-1])

    def test_missing_values_sort_last_in_both_directions(self):
        for direction in (Order.ASC, Order.DESC):
            order = self.repository._anime_order(AnimeSort.SCORE, direction, None)
            assert "NULLS LAST" in sql(order[0])

    def test_default_directions(self):
        assert "DESC" in sql(self.repository._anime_order(AnimeSort.MEMBERS, None, None)[0])
        assert "ASC" in sql(self.repository._anime_order(AnimeSort.TITLE, None, None)[0])
        assert "DESC" in sql(self.repository._anime_order(AnimeSort.NEWEST, None, None)[0])

    def test_an_explicit_order_overrides_the_default(self):
        assert "ASC" in sql(self.repository._anime_order(AnimeSort.MEMBERS, Order.ASC, None)[0])
        assert "DESC" in sql(self.repository._anime_order(AnimeSort.TITLE, Order.DESC, None)[0])

    def test_relevance_puts_an_exact_title_first_then_a_prefix_then_members(self):
        order = self.repository._anime_order(AnimeSort.RELEVANCE, None, "titan")
        text = sql(order[0])

        assert "CASE WHEN" in text
        assert "lower(anime.title) = 'titan'" in text
        assert "members_mal DESC" in sql(order[1])
