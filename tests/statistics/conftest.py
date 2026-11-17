from decimal import Decimal

import pytest

from app.catalogue.models import Anime, AnimeStatisticsMal, Review, Studio, Tag
from app.catalogue.repository import CatalogueRepository, CohortStanding
from app.main import app
from app.statistics.deps import create_statistics_service
from app.statistics.service import StatisticsService


@pytest.fixture
def make_anime():
    def _make(**overrides):
        fields = {
            "mal_id": 1,
            "title": "Some Anime",
            "type": "TV",
            "year": 2025,
            "status": "Finished Airing",
            "mal_score": Decimal("8.10"),
            "scored_by_mal": 5000,
            "members_mal": 10000,
            "favorites_mal": 500,
            "rank_mal": 100,
            "popularity_mal": 200,
        }
        fields.update(overrides)
        return Anime(**fields)

    return _make


@pytest.fixture
def make_statistics():
    def _make(votes=None, **overrides):
        votes = votes if votes is not None else [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        fields = {
            "anime_id": 1,
            "watching": 1000,
            "completed": 6000,
            "on_hold": 500,
            "dropped": 500,
            "plan_to_watch": 2000,
            "total": 10000,
            **{f"votes_{score}": count for score, count in zip(range(1, 11), votes, strict=True)},
        }
        fields.update(overrides)
        return AnimeStatisticsMal(**fields)

    return _make


@pytest.fixture
def make_review():
    def _make(**overrides):
        fields = {
            "mal_id": 1,
            "anime_id": 1,
            "score": 9,
            "review": "Great show.",
            "username": "reviewer",
            "is_spoiler": False,
            "is_preliminary": False,
            "reaction_overall": 10,
        }
        fields.update(overrides)
        return Review(**fields)

    return _make


@pytest.fixture
def make_standing():
    """A cohort standing; every metric defaults to a cohort of 100 where this anime is strong."""

    def _make(**overrides):
        fields = {
            "cohort_size": 100,
            "score_count": 100,
            "score_rank": 10,
            "score_percentile": 0.90,
            "popularity_count": 100,
            "popularity_rank": 95,
            "popularity_percentile": 0.10,
            "favorites_count": 100,
            "favorites_rank": 5,
            "favorites_percentile": 0.95,
        }
        fields.update(overrides)
        return CohortStanding(**fields)

    return _make


@pytest.fixture
def mock_catalogue_repository(mocker, make_anime, make_statistics, make_standing):
    """An autospecced repository that returns a complete, healthy anime by default."""
    repository = mocker.create_autospec(CatalogueRepository, instance=True)
    repository.get_anime_with_statistics.return_value = (make_anime(), make_statistics())
    repository.get_tags_of_anime.return_value = [Tag(mal_id=1, name="Action")]
    repository.get_studios_of_anime.return_value = [Studio(mal_id=2, name="Studio")]
    repository.get_reviews_of_anime.return_value = []
    repository.get_cohort_standing.return_value = make_standing()
    return repository


@pytest.fixture
def statistics_service(mock_catalogue_repository):
    return StatisticsService(mock_catalogue_repository)


@pytest.fixture
def fake_statistics_service(mocker, client):
    fake = mocker.create_autospec(StatisticsService, instance=True)
    app.dependency_overrides[create_statistics_service] = lambda: fake
    return fake
