from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.statistics.exceptions import AnimeNotFoundError
from app.statistics.service import LOW_SAMPLE_VOTES


class TestGetAnimeStatistics:
    async def test_unknown_anime_raises_not_found(
        self, statistics_service, mock_catalogue_repository
    ):
        mock_catalogue_repository.get_anime_with_statistics.return_value = None

        with pytest.raises(AnimeNotFoundError):
            await statistics_service.get_anime_statistics(999)

        mock_catalogue_repository.get_tags_of_anime.assert_not_called()

    async def test_success_builds_every_block(self, statistics_service, make_review, mocker):
        statistics_service.catalogue_repository.get_reviews_of_anime.return_value = [make_review()]

        result = await statistics_service.get_anime_statistics(1)

        assert result.core.mal_id == 1
        assert result.core.mal_score == 8.10
        assert [tag.name for tag in result.tags] == ["Action"]
        assert [studio.name for studio in result.studios] == ["Studio"]
        assert len(result.reviews.items) == 1
        assert result.reviews.sample_note
        stats = result.statistics
        assert stats.list_status.total == 10000
        assert stats.score_distribution.total_votes == 550
        assert stats.polarization.standard_deviation is not None
        assert stats.cohort.cohort_size == 100

    async def test_review_keeps_its_spoiler_flag(self, statistics_service, make_review):
        statistics_service.catalogue_repository.get_reviews_of_anime.return_value = [
            make_review(is_spoiler=True)
        ]

        result = await statistics_service.get_anime_statistics(1)

        assert result.reviews.items[0].is_spoiler is True

    async def test_missing_statistics_row_gives_null_statistics_but_keeps_the_rest(
        self, statistics_service, mock_catalogue_repository, make_anime, make_review
    ):
        mock_catalogue_repository.get_anime_with_statistics.return_value = (make_anime(), None)
        mock_catalogue_repository.get_reviews_of_anime.return_value = [make_review()]

        result = await statistics_service.get_anime_statistics(1)

        assert result.statistics is None
        assert result.core.title == "Some Anime"
        assert len(result.tags) == 1
        assert len(result.reviews.items) == 1
        mock_catalogue_repository.get_cohort_standing.assert_not_called()

    async def test_null_votes_give_null_derived_fields_not_an_error(
        self, statistics_service, mock_catalogue_repository, make_anime, make_statistics
    ):
        row = make_statistics()
        for score in range(1, 11):
            setattr(row, f"votes_{score}", None)
        mock_catalogue_repository.get_anime_with_statistics.return_value = (make_anime(), row)

        stats = (await statistics_service.get_anime_statistics(1)).statistics

        assert stats.score_distribution.mean is None
        assert stats.score_distribution.mode is None
        assert stats.polarization.standard_deviation is None
        assert stats.polarization.low_vote_share is None

    async def test_null_score_and_counters_give_null_ratios_not_an_error(
        self, statistics_service, mock_catalogue_repository, make_anime, make_statistics
    ):
        anime = make_anime(mal_score=None, scored_by_mal=None, members_mal=None, favorites_mal=None)
        mock_catalogue_repository.get_anime_with_statistics.return_value = (
            anime,
            make_statistics(),
        )

        result = await statistics_service.get_anime_statistics(1)

        engagement = result.statistics.engagement
        assert result.core.mal_score is None
        assert engagement.favorites_per_member is None
        assert engagement.scored_by_ratio is None
        assert engagement.plan_to_watch_share is None
        assert engagement.low_sample is None

    async def test_engagement_ratios_use_members(self, statistics_service):
        engagement = (await statistics_service.get_anime_statistics(1)).statistics.engagement

        assert engagement.favorites_per_member == 500 / 10000
        assert engagement.scored_by_ratio == 5000 / 10000
        assert engagement.plan_to_watch_share == 2000 / 10000

    async def test_zero_members_gives_null_ratios(
        self, statistics_service, mock_catalogue_repository, make_anime, make_statistics
    ):
        mock_catalogue_repository.get_anime_with_statistics.return_value = (
            make_anime(members_mal=0),
            make_statistics(),
        )

        engagement = (await statistics_service.get_anime_statistics(1)).statistics.engagement

        assert engagement.favorites_per_member is None
        assert engagement.plan_to_watch_share is None

    @pytest.mark.parametrize(
        "scored_by,expected",
        [(LOW_SAMPLE_VOTES - 1, True), (LOW_SAMPLE_VOTES, False)],
    )
    async def test_low_sample_boundary(
        self,
        statistics_service,
        mock_catalogue_repository,
        make_anime,
        make_statistics,
        scored_by,
        expected,
    ):
        mock_catalogue_repository.get_anime_with_statistics.return_value = (
            make_anime(scored_by_mal=scored_by),
            make_statistics(),
        )

        engagement = (await statistics_service.get_anime_statistics(1)).statistics.engagement

        assert engagement.low_sample is expected


class TestCohort:
    async def test_passes_the_anime_type_and_year_to_the_repository(
        self, statistics_service, mock_catalogue_repository
    ):
        await statistics_service.get_anime_statistics(1)

        mock_catalogue_repository.get_cohort_standing.assert_awaited_once_with(1, "TV", 2025, 2025)

    async def test_ranks_and_percentiles_are_returned(self, statistics_service):
        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert cohort.type == "TV"
        assert (cohort.year_from, cohort.year_to) == (2025, 2025)
        assert cohort.score.rank == 10
        assert cohort.score.percentile == 0.90
        assert cohort.popularity.percentile == 0.10
        assert cohort.favorites.rank == 5

    async def test_drop_rate_and_completion_rate_get_their_own_rank_and_percentile(
        self, statistics_service
    ):
        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert (cohort.drop_rate.rank, cohort.drop_rate.percentile) == (20, 0.80)
        assert (cohort.completion_rate.rank, cohort.completion_rate.percentile) == (8, 0.92)

    async def test_completion_rate_is_null_when_the_anime_has_none_such_as_while_airing(
        self, statistics_service, mock_catalogue_repository, make_standing
    ):
        mock_catalogue_repository.get_cohort_standing.return_value = make_standing(
            completion_rate_rank=None, completion_rate_percentile=None
        )

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert cohort.completion_rate is None
        assert cohort.drop_rate is not None

    async def test_drop_rate_with_fewer_than_30_rows_is_null_but_the_rest_stay(
        self, statistics_service, mock_catalogue_repository, make_standing
    ):
        mock_catalogue_repository.get_cohort_standing.return_value = make_standing(
            drop_rate_count=12
        )

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert cohort.drop_rate is None
        assert cohort.score is not None

    async def test_a_metric_with_fewer_than_30_rows_is_null(
        self, statistics_service, mock_catalogue_repository, make_standing
    ):
        mock_catalogue_repository.get_cohort_standing.return_value = make_standing(score_count=29)

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert cohort.score is None
        assert cohort.popularity is not None  # judged on its own count
        assert cohort.hidden_gem_gap is None
        assert cohort.is_hidden_gem is None

    async def test_exactly_30_rows_is_enough(
        self, statistics_service, mock_catalogue_repository, make_standing
    ):
        mock_catalogue_repository.get_cohort_standing.return_value = make_standing(
            score_count=30, popularity_count=30, favorites_count=30
        )

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert cohort.score is not None

    async def test_cohort_size_is_still_reported_when_metrics_are_null(
        self, statistics_service, mock_catalogue_repository, make_standing
    ):
        mock_catalogue_repository.get_cohort_standing.return_value = make_standing(
            cohort_size=12, score_count=12, popularity_count=12, favorites_count=12
        )

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert cohort.cohort_size == 12
        assert cohort.score is None
        assert cohort.popularity is None
        assert cohort.favorites is None

    async def test_anime_without_a_value_for_a_metric_gets_null_for_it(
        self, statistics_service, mock_catalogue_repository, make_standing
    ):
        mock_catalogue_repository.get_cohort_standing.return_value = make_standing(
            score_rank=None, score_percentile=None
        )

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert cohort.score is None
        assert cohort.popularity is not None

    async def test_no_type_or_year_means_no_cohort(
        self, statistics_service, mock_catalogue_repository, make_anime, make_statistics
    ):
        mock_catalogue_repository.get_anime_with_statistics.return_value = (
            make_anime(type=None, year=None),
            make_statistics(),
        )
        mock_catalogue_repository.get_cohort_standing.return_value = None

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert cohort.cohort_size == 0
        assert cohort.score is None
        assert cohort.is_hidden_gem is None


class TestCohortYears:
    """The cohort covers every year the anime aired, from its start year to the year it ended."""

    async def test_a_finished_long_runner_uses_its_end_year(
        self, statistics_service, mock_catalogue_repository, make_anime, make_statistics
    ):
        anime = make_anime(year=1999, aired_to=datetime(2006, 3, 1, tzinfo=UTC))
        mock_catalogue_repository.get_anime_with_statistics.return_value = (
            anime,
            make_statistics(),
        )

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        mock_catalogue_repository.get_cohort_standing.assert_awaited_once_with(1, "TV", 1999, 2006)
        assert (cohort.year_from, cohort.year_to) == (1999, 2006)

    async def test_a_show_still_airing_runs_to_the_current_year(
        self, statistics_service, mock_catalogue_repository, make_anime, make_statistics, mocker
    ):
        mocker.patch("app.statistics.service._current_year", return_value=2031)
        anime = make_anime(year=1999, status="Currently Airing", aired_to=None)
        mock_catalogue_repository.get_anime_with_statistics.return_value = (
            anime,
            make_statistics(),
        )

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert (cohort.year_from, cohort.year_to) == (1999, 2031)

    async def test_a_show_with_no_end_date_that_is_not_airing_is_one_year(
        self, statistics_service, mock_catalogue_repository, make_anime, make_statistics
    ):
        anime = make_anime(year=2011, status="Finished Airing", aired_to=None)
        mock_catalogue_repository.get_anime_with_statistics.return_value = (
            anime,
            make_statistics(),
        )

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert (cohort.year_from, cohort.year_to) == (2011, 2011)

    async def test_an_end_date_before_the_start_year_never_shrinks_the_range(
        self, statistics_service, mock_catalogue_repository, make_anime, make_statistics
    ):
        anime = make_anime(year=2011, aired_to=datetime(2010, 1, 1, tzinfo=UTC))
        mock_catalogue_repository.get_anime_with_statistics.return_value = (
            anime,
            make_statistics(),
        )

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert (cohort.year_from, cohort.year_to) == (2011, 2011)

    async def test_no_start_year_gives_no_range(
        self, statistics_service, mock_catalogue_repository, make_anime, make_statistics
    ):
        mock_catalogue_repository.get_anime_with_statistics.return_value = (
            make_anime(year=None),
            make_statistics(),
        )
        mock_catalogue_repository.get_cohort_standing.return_value = None

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert (cohort.year_from, cohort.year_to) == (None, None)
        assert cohort.cohort_size == 0


class TestHiddenGem:
    async def test_high_score_and_low_popularity_is_a_gem(self, statistics_service):
        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert cohort.hidden_gem_gap == pytest.approx(0.80)
        assert cohort.is_hidden_gem is True

    @pytest.mark.parametrize(
        "score_percentile,popularity_percentile,expected",
        [
            (0.80, 0.30, True),  # both thresholds are inclusive
            (0.79, 0.10, False),  # score not high enough
            (0.95, 0.31, False),  # too popular
            (0.20, 0.90, False),  # popular but poorly rated: the opposite of a gem
        ],
    )
    async def test_thresholds(
        self,
        statistics_service,
        mock_catalogue_repository,
        make_standing,
        score_percentile,
        popularity_percentile,
        expected,
    ):
        mock_catalogue_repository.get_cohort_standing.return_value = make_standing(
            score_percentile=score_percentile, popularity_percentile=popularity_percentile
        )

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert cohort.is_hidden_gem is expected
        assert cohort.hidden_gem_gap == pytest.approx(score_percentile - popularity_percentile)

    async def test_low_sample_anime_is_never_a_gem(
        self, statistics_service, mock_catalogue_repository, make_anime, make_statistics
    ):
        mock_catalogue_repository.get_anime_with_statistics.return_value = (
            make_anime(scored_by_mal=40, mal_score=Decimal("9.50")),
            make_statistics(),
        )

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert cohort.hidden_gem_gap == pytest.approx(0.80)  # the number is still shown
        assert cohort.is_hidden_gem is False

    async def test_unknown_vote_count_is_not_a_gem(
        self, statistics_service, mock_catalogue_repository, make_anime, make_statistics
    ):
        mock_catalogue_repository.get_anime_with_statistics.return_value = (
            make_anime(scored_by_mal=None),
            make_statistics(),
        )

        cohort = (await statistics_service.get_anime_statistics(1)).statistics.cohort

        assert cohort.is_hidden_gem is False
