import pytest

from app.catalogue.enums import AnimeType
from app.exceptions import BadRequestError, ServiceUnavailableError
from app.main import app
from app.statistics.deps import create_list_service
from app.statistics.list_schemas import (
    HiddenGemRowSchema,
    HiddenGemsResponse,
    MostDroppedResponse,
    MostDroppedRowSchema,
)
from app.statistics.list_service import ListService

BASE = "/api/v1/statistics"


@pytest.fixture
def fake_service(mocker, client):
    fake = mocker.create_autospec(ListService, instance=True)
    fake.hidden_gems.return_value = HiddenGemsResponse(
        min_ratings=50,
        score_percentile=0.8,
        members_percentile=0.5,
        min_scored_by=2000,
        total=1,
        limit=20,
        offset=0,
        items=[
            HiddenGemRowSchema(
                mal_id=1,
                title="A Gem",
                type="tv",
                year=2011,
                mal_score=8.7,
                members_mal=15000,
                scored_by_mal=4200,
                score_percentile=0.99,
                members_percentile=0.25,
            )
        ],
    )
    fake.most_dropped.return_value = MostDroppedResponse(
        min_members=10000,
        total=1,
        limit=20,
        offset=0,
        items=[
            MostDroppedRowSchema(
                mal_id=2,
                title="Dropped Often",
                type="tv",
                year=2015,
                mal_score=5.5,
                members_mal=40000,
                dropped=12000,
                started=30000,
                drop_rate=0.4,
            )
        ],
    )
    app.dependency_overrides[create_list_service] = lambda: fake
    return fake


class TestResponses:
    def test_hidden_gems(self, client, fake_service):
        response = client.get(f"{BASE}/hidden-gems")

        assert response.status_code == 200
        body = response.json()
        HiddenGemsResponse.model_validate(body)
        assert body["min_scored_by"] == 2000
        assert body["items"][0]["score_percentile"] == 0.99

    def test_most_dropped(self, client, fake_service):
        response = client.get(f"{BASE}/most-dropped")

        assert response.status_code == 200
        body = response.json()
        MostDroppedResponse.model_validate(body)
        assert body["items"][0]["drop_rate"] == 0.4


class TestParameters:
    def test_defaults_reach_the_service(self, client, fake_service):
        client.get(f"{BASE}/hidden-gems")
        client.get(f"{BASE}/most-dropped")

        assert fake_service.hidden_gems.call_args.args == (None, None, None, 20, 0, 50)
        assert fake_service.most_dropped.call_args.args == (None, None, None, 20, 0, 10_000)

    def test_the_filters_and_thresholds_reach_the_service(self, client, fake_service):
        client.get(
            f"{BASE}/hidden-gems",
            params={
                "type": "movie",
                "year_from": 2000,
                "year_to": 2010,
                "limit": 5,
                "offset": 10,
                "min_ratings": 300,
            },
        )
        client.get(f"{BASE}/most-dropped", params={"type": "ova", "min_members": 500})

        assert fake_service.hidden_gems.call_args.args == (
            AnimeType.MOVIE,
            2000,
            2010,
            5,
            10,
            300,
        )
        assert fake_service.most_dropped.call_args.args == (AnimeType.OVA, None, None, 20, 0, 500)

    @pytest.mark.parametrize("path", ["hidden-gems", "most-dropped"])
    @pytest.mark.parametrize(
        "params",
        [
            {"type": "cartoon"},
            {"limit": 0},
            {"limit": 101},
            {"offset": -1},
            {"offset": 10_001},
            {"year_from": 1899},
            {"year_to": 2101},
        ],
    )
    def test_invalid_parameters_return_422_and_never_reach_the_service(
        self, client, fake_service, path, params
    ):
        assert client.get(f"{BASE}/{path}", params=params).status_code == 422
        fake_service.hidden_gems.assert_not_called()
        fake_service.most_dropped.assert_not_called()

    @pytest.mark.parametrize(
        ("path", "params"),
        [
            ("hidden-gems", {"min_ratings": -1}),
            ("hidden-gems", {"min_ratings": 100_001}),
            ("most-dropped", {"min_members": -1}),
            ("most-dropped", {"min_members": 10_000_001}),
        ],
    )
    def test_a_threshold_outside_its_range_is_422(self, client, fake_service, path, params):
        assert client.get(f"{BASE}/{path}", params=params).status_code == 422


class TestErrors:
    def test_a_bad_request_from_the_service_is_a_400(self, client, fake_service):
        fake_service.hidden_gems.side_effect = BadRequestError(
            "year_from must not be after year_to."
        )

        assert client.get(f"{BASE}/hidden-gems", params={"year_from": 2020}).status_code == 400

    def test_a_timeout_is_a_503(self, client, fake_service):
        fake_service.most_dropped.side_effect = ServiceUnavailableError()

        assert client.get(f"{BASE}/most-dropped").status_code == 503


class TestOpenApi:
    def test_the_descriptions_state_the_thresholds(self, client, fake_service):
        paths = client.get("/openapi.json").json()["paths"]

        gems = paths[f"{BASE}/hidden-gems"]["get"]["description"]
        assert "top 20%" in gems and "bottom 50%" in gems and "2,000" in gems
        assert "10,000" in paths[f"{BASE}/most-dropped"]["get"]["description"]
