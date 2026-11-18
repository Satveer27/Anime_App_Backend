from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.anime.deps import create_anime_list_service, create_options_service
from app.anime.option_service import OptionsService
from app.anime.service import AnimeListService
from app.catalogue.enums import TagMatch
from app.catalogue.read_repository import CatalogueReadRepository
from app.main import app


@pytest.fixture
def make_row():
    """A row as the list query returns it: attribute access, MAL's stored texts."""

    def _make(mal_id=1, title="Some Anime", **overrides):
        fields = {
            "mal_id": mal_id,
            "title": title,
            "title_english": None,
            "title_japanese": None,
            "type": "TV",
            "status": "Finished Airing",
            "season": "fall",
            "year": 2020,
            "episodes": 12,
            "aired_from": datetime(2020, 10, 1, tzinfo=UTC),
            "mal_score": Decimal("8.10"),
            "scored_by_mal": 5000,
            "members_mal": 100000,
            "favorites_mal": 2000,
            "rank_mal": 100,
            "popularity_mal": 200,
            "image_url": "https://cdn.example/x.jpg",
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    return _make


@pytest.fixture
def mock_read_repository(mocker):
    repository = mocker.create_autospec(CatalogueReadRepository, instance=True)
    repository.search_anime.return_value = ([], 0)
    repository.list_tags.return_value = []
    repository.year_range.return_value = (None, None)
    repository.list_studios.return_value = ([], 0)
    return repository


@pytest.fixture
def anime_list_service(mock_read_repository):
    return AnimeListService(mock_read_repository)


@pytest.fixture
def options_service(mock_read_repository):
    return OptionsService(mock_read_repository)


@pytest.fixture
def fake_options_service(mocker, client):
    fake = mocker.create_autospec(OptionsService, instance=True)
    app.dependency_overrides[create_options_service] = lambda: fake
    return fake


@pytest.fixture
def fake_anime_list_service(mocker, client):
    fake = mocker.create_autospec(AnimeListService, instance=True)
    app.dependency_overrides[create_anime_list_service] = lambda: fake
    return fake


def all_none_args(**overrides):
    """Every keyword the service takes, unset, for the test to override."""
    args = {
        "q": None,
        "type": None,
        "status": None,
        "season": None,
        "year": None,
        "year_from": None,
        "year_to": None,
        "tag_id": None,
        "tag_match": TagMatch.ALL,
        "studio_id": None,
        "min_score": None,
        "min_ratings": None,
        "sort": None,
        "order": None,
        "limit": 20,
        "offset": 0,
    }
    args.update(overrides)
    return args
