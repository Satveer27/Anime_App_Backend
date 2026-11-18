from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import DBAPIError
from structlog.testing import capture_logs

from app.anime.service import released_since
from app.catalogue.enums import (
    AnimeSeason,
    AnimeSort,
    AnimeStatus,
    AnimeType,
    Order,
    ReleaseStatus,
    TagMatch,
)
from app.catalogue.read_repository import AnimeFilters
from app.exceptions import BadRequestError, ServiceUnavailableError
from tests.anime.conftest import all_none_args


class _PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__("pg error")
        self.sqlstate = sqlstate


def sent_filters(mock_read_repository) -> AnimeFilters:
    return mock_read_repository.search_anime.call_args.args[0]


class TestFilters:
    async def test_enum_filters_become_the_stored_texts(
        self, anime_list_service, mock_read_repository
    ):
        await anime_list_service.list_anime(
            **all_none_args(
                type=[AnimeType.TV_SPECIAL],
                status=[AnimeStatus.AIRING],
                season=[AnimeSeason.FALL],
                year=2020,
                tag_id=[7],
                studio_id=[9],
                min_score=7.5,
                min_ratings=500,
            )
        )

        assert sent_filters(mock_read_repository) == AnimeFilters(
            types=("TV Special",),
            statuses=("Currently Airing",),
            seasons=("fall",),
            year=2020,
            tag_ids=(7,),
            studio_ids=(9,),
            min_score=7.5,
            min_ratings=500,
        )

    @pytest.mark.parametrize(
        "status,stored",
        [
            (AnimeStatus.AIRING, "Currently Airing"),
            (AnimeStatus.FINISHED, "Finished Airing"),
            (AnimeStatus.UPCOMING, "Not yet aired"),
        ],
    )
    async def test_every_status_maps(
        self, anime_list_service, mock_read_repository, status, stored
    ):
        await anime_list_service.list_anime(**all_none_args(status=[status]))

        assert sent_filters(mock_read_repository).statuses == (stored,)

    @pytest.mark.parametrize("anime_type,stored", [
        (AnimeType.TV, "TV"), (AnimeType.MOVIE, "Movie"), (AnimeType.OVA, "OVA"),
        (AnimeType.SPECIAL, "Special"), (AnimeType.ONA, "ONA"),
    ])  # fmt: skip
    async def test_every_type_maps(
        self, anime_list_service, mock_read_repository, anime_type, stored
    ):
        await anime_list_service.list_anime(**all_none_args(type=[anime_type]))

        assert sent_filters(mock_read_repository).types == (stored,)

    async def test_no_filters_sends_empty_filters(self, anime_list_service, mock_read_repository):
        await anime_list_service.list_anime(**all_none_args())

        assert sent_filters(mock_read_repository) == AnimeFilters()

    async def test_search_text_has_its_spaces_collapsed(
        self, anime_list_service, mock_read_repository
    ):
        await anime_list_service.list_anime(**all_none_args(q="  attack   on  titan "))

        assert sent_filters(mock_read_repository).q == "attack on titan"

    async def test_a_blank_search_text_is_no_search(self, anime_list_service, mock_read_repository):
        await anime_list_service.list_anime(**all_none_args(q="   "))

        filters, sort = mock_read_repository.search_anime.call_args.args[:2]
        assert filters.q is None
        assert sort == AnimeSort.MEMBERS


class TestSeveralValues:
    async def test_several_values_of_a_filter_are_all_sent(
        self, anime_list_service, mock_read_repository
    ):
        await anime_list_service.list_anime(
            **all_none_args(
                type=[AnimeType.TV, AnimeType.MOVIE],
                status=[AnimeStatus.AIRING, AnimeStatus.UPCOMING],
                season=[AnimeSeason.FALL, AnimeSeason.WINTER],
                tag_id=[4, 1],
                studio_id=[9, 2],
            )
        )

        filters = sent_filters(mock_read_repository)
        assert filters.types == ("Movie", "TV")
        assert filters.statuses == ("Currently Airing", "Not yet aired")
        assert filters.seasons == ("fall", "winter")
        assert filters.tag_ids == (1, 4)
        assert filters.studio_ids == (2, 9)

    async def test_repeated_values_are_sent_once(self, anime_list_service, mock_read_repository):
        await anime_list_service.list_anime(
            **all_none_args(
                status=[AnimeStatus.AIRING, AnimeStatus.AIRING], tag_id=[3, 3, 3], studio_id=[5, 5]
            )
        )

        filters = sent_filters(mock_read_repository)
        assert filters.statuses == ("Currently Airing",)
        assert filters.tag_ids == (3,)
        assert filters.studio_ids == (5,)

    async def test_tags_must_all_match_by_default(self, anime_list_service, mock_read_repository):
        await anime_list_service.list_anime(**all_none_args(tag_id=[1, 2]))

        assert sent_filters(mock_read_repository).tag_match_all is True

    async def test_tag_match_any_is_passed_on(self, anime_list_service, mock_read_repository):
        await anime_list_service.list_anime(**all_none_args(tag_id=[1, 2], tag_match=TagMatch.ANY))

        assert sent_filters(mock_read_repository).tag_match_all is False


class TestReleaseStatus:
    @pytest.mark.parametrize("release_status", list(ReleaseStatus))
    async def test_the_release_status_is_passed_on(
        self, anime_list_service, mock_read_repository, release_status
    ):
        await anime_list_service.list_anime(**all_none_args(release_status=release_status))

        assert sent_filters(mock_read_repository).release_status is release_status

    async def test_without_it_the_filter_is_off(self, anime_list_service, mock_read_repository):
        await anime_list_service.list_anime(**all_none_args())

        assert sent_filters(mock_read_repository).release_status is None

    async def test_the_latest_episode_sort_is_accepted(
        self, anime_list_service, mock_read_repository
    ):
        await anime_list_service.list_anime(**all_none_args(sort=AnimeSort.LATEST_EPISODE))

        assert mock_read_repository.search_anime.call_args.args[1] == AnimeSort.LATEST_EPISODE

    async def test_the_filter_is_logged_by_name_only(self, anime_list_service):
        with capture_logs() as logs:
            await anime_list_service.list_anime(
                **all_none_args(release_status=ReleaseStatus.ON_BREAK)
            )

        served = [e for e in logs if e["event"] == "anime_list_served"]
        assert served[0]["release_status"] == "on_break"


class TestReleasedSince:
    """`released_days=N`: released an episode in the last N calendar days, today included."""

    def at(self, year, month, day):
        return datetime(year, month, day, tzinfo=UTC)

    def test_no_days_means_no_filter(self):
        assert released_since(None, date(2026, 11, 22)) is None

    def test_a_week_called_on_a_sunday_is_monday_to_sunday(self):
        sunday = date(2026, 11, 22)
        assert sunday.strftime("%A") == "Sunday"

        assert released_since(7, sunday) == self.at(2026, 11, 16)  # the Monday of that week

    def test_the_episode_that_aired_the_sunday_before_is_the_previous_weeks(self):
        since = released_since(7, date(2026, 11, 22))

        assert self.at(2026, 11, 15) < since  # last Sunday, midnight UTC, is outside
        assert self.at(2026, 11, 16) >= since  # Monday is inside

    def test_one_day_is_today(self):
        assert released_since(1, date(2026, 11, 18)) == self.at(2026, 11, 18)

    def test_a_week_called_midweek(self):
        assert released_since(7, date(2026, 11, 18)) == self.at(2026, 11, 12)

    def test_it_counts_back_over_a_month_and_a_year_boundary(self):
        assert released_since(7, date(2026, 3, 3)) == self.at(2026, 2, 25)
        assert released_since(7, date(2026, 1, 3)) == self.at(2025, 12, 28)

    def test_the_window_starts_at_midnight_utc(self):
        since = released_since(30, date(2026, 11, 18))

        assert (since.hour, since.minute, since.second, since.tzinfo) == (0, 0, 0, UTC)

    async def test_the_service_turns_the_days_into_a_start_date(
        self, anime_list_service, mock_read_repository, mocker
    ):
        mocker.patch("app.anime.service._utc_today", return_value=date(2026, 11, 22))

        await anime_list_service.list_anime(**all_none_args(released_days=7))

        assert sent_filters(mock_read_repository).released_since == self.at(2026, 11, 16)

    async def test_without_released_days_the_filter_is_off(
        self, anime_list_service, mock_read_repository
    ):
        await anime_list_service.list_anime(**all_none_args())

        assert sent_filters(mock_read_repository).released_since is None

    async def test_it_combines_with_the_other_filters(
        self, anime_list_service, mock_read_repository, mocker
    ):
        mocker.patch("app.anime.service._utc_today", return_value=date(2026, 11, 22))

        await anime_list_service.list_anime(
            **all_none_args(
                released_days=7, status=[AnimeStatus.AIRING], sort=AnimeSort.LATEST_EPISODE
            )
        )

        filters, sort = mock_read_repository.search_anime.call_args.args[:2]
        assert filters.released_since == self.at(2026, 11, 16)
        assert filters.statuses == ("Currently Airing",)
        assert sort == AnimeSort.LATEST_EPISODE


class TestSortingAndPaging:
    async def test_default_sort_is_members_without_a_search(
        self, anime_list_service, mock_read_repository
    ):
        await anime_list_service.list_anime(**all_none_args())

        assert mock_read_repository.search_anime.call_args.args[1] == AnimeSort.MEMBERS

    async def test_default_sort_is_relevance_with_a_search(
        self, anime_list_service, mock_read_repository
    ):
        await anime_list_service.list_anime(**all_none_args(q="titan"))

        assert mock_read_repository.search_anime.call_args.args[1] == AnimeSort.RELEVANCE

    async def test_an_explicit_sort_and_order_are_kept_with_a_search(
        self, anime_list_service, mock_read_repository
    ):
        await anime_list_service.list_anime(
            **all_none_args(q="titan", sort=AnimeSort.SCORE, order=Order.ASC)
        )

        args = mock_read_repository.search_anime.call_args.args
        assert args[1] == AnimeSort.SCORE
        assert args[2] == Order.ASC

    async def test_limit_and_offset_are_passed_on(self, anime_list_service, mock_read_repository):
        await anime_list_service.list_anime(**all_none_args(limit=5, offset=40))

        args = mock_read_repository.search_anime.call_args.args
        assert (args[3], args[4]) == (5, 40)


class TestValidation:
    async def test_relevance_without_a_search_is_a_bad_request(
        self, anime_list_service, mock_read_repository
    ):
        with pytest.raises(BadRequestError):
            await anime_list_service.list_anime(**all_none_args(sort=AnimeSort.RELEVANCE))

        mock_read_repository.search_anime.assert_not_called()

    async def test_year_together_with_a_range_is_a_bad_request(self, anime_list_service):
        with pytest.raises(BadRequestError):
            await anime_list_service.list_anime(**all_none_args(year=2020, year_from=2019))
        with pytest.raises(BadRequestError):
            await anime_list_service.list_anime(**all_none_args(year=2020, year_to=2021))

    async def test_a_backwards_range_is_a_bad_request(self, anime_list_service):
        with pytest.raises(BadRequestError):
            await anime_list_service.list_anime(**all_none_args(year_from=2021, year_to=2019))

    async def test_a_single_year_bound_is_fine(self, anime_list_service, mock_read_repository):
        await anime_list_service.list_anime(**all_none_args(year_from=2019))

        assert sent_filters(mock_read_repository).year_from == 2019


class TestResponse:
    async def test_rows_become_items_with_the_total(
        self, anime_list_service, mock_read_repository, make_row
    ):
        mock_read_repository.search_anime.return_value = (
            [make_row(1, "A"), make_row(2, "B", mal_score=None)],
            57,
        )

        result = await anime_list_service.list_anime(**all_none_args(limit=2, offset=4))

        assert [item.title for item in result.items] == ["A", "B"]
        assert result.items[0].mal_score == 8.10
        assert result.items[1].mal_score is None
        assert (result.total, result.limit, result.offset) == (57, 2, 4)

    async def test_each_item_gets_the_date_of_its_latest_episode(
        self, anime_list_service, mock_read_repository, make_row
    ):
        aired = datetime(2026, 11, 13, tzinfo=UTC)
        mock_read_repository.search_anime.return_value = ([make_row(1, "A"), make_row(2, "B")], 2)
        mock_read_repository.last_episode_dates.return_value = {1: aired}

        result = await anime_list_service.list_anime(**all_none_args())

        assert result.items[0].last_episode_aired == aired
        assert result.items[1].last_episode_aired is None  # no episodes held for it
        mock_read_repository.last_episode_dates.assert_awaited_once_with([1, 2])

    async def test_no_matches_is_an_empty_page_not_an_error(
        self, anime_list_service, mock_read_repository
    ):
        result = await anime_list_service.list_anime(**all_none_args(q="zzzzzz"))

        assert result.items == []
        assert result.total == 0
        mock_read_repository.last_episode_dates.assert_awaited_once_with([])

    async def test_the_search_text_is_never_logged(self, anime_list_service):
        with capture_logs() as logs:
            await anime_list_service.list_anime(**all_none_args(q="my secret search"))

        served = [entry for entry in logs if entry["event"] == "anime_list_served"]
        assert len(served) == 1
        assert served[0]["has_search"] is True
        assert "my secret search" not in str(logs)


class TestTimeout:
    async def test_a_statement_timeout_becomes_a_503_and_is_logged_once(
        self, anime_list_service, mock_read_repository
    ):
        mock_read_repository.search_anime.side_effect = DBAPIError("SELECT", {}, _PgError("57014"))

        with capture_logs() as logs, pytest.raises(ServiceUnavailableError):
            await anime_list_service.list_anime(**all_none_args())

        assert [e["event"] for e in logs].count("statement_timeout") == 1

    async def test_a_timeout_while_reading_the_episode_dates_is_a_503_too(
        self, anime_list_service, mock_read_repository, make_row
    ):
        mock_read_repository.search_anime.return_value = ([make_row(1)], 1)
        mock_read_repository.last_episode_dates.side_effect = DBAPIError(
            "SELECT", {}, _PgError("57014")
        )

        with pytest.raises(ServiceUnavailableError):
            await anime_list_service.list_anime(**all_none_args())

    async def test_any_other_database_error_is_not_swallowed(
        self, anime_list_service, mock_read_repository
    ):
        mock_read_repository.search_anime.side_effect = DBAPIError("SELECT", {}, _PgError("42P01"))

        with pytest.raises(DBAPIError):
            await anime_list_service.list_anime(**all_none_args())
