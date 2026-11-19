import pytest

from app.config import settings

ORIGIN = settings.frontend_url.rstrip("/")
OTHER = "https://evil.example.com"
URL = "/api/v1/health/live"


def preflight(client, origin, method="GET", headers="authorization"):
    return client.options(
        URL,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )


class TestPreflight:
    def test_the_frontend_origin_is_allowed_with_credentials(self, client):
        response = preflight(client, ORIGIN)

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["access-control-allow-credentials"] == "true"

    def test_another_origin_is_refused(self, client):
        response = preflight(client, OTHER)

        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers

    @pytest.mark.parametrize("origin", ["null", "http://localhost:9999", ORIGIN + ".evil.com"])
    def test_lookalike_origins_are_refused(self, client, origin):
        assert "access-control-allow-origin" not in preflight(client, origin).headers

    def test_a_header_the_frontend_does_not_use_is_refused(self, client):
        assert preflight(client, ORIGIN, headers="x-custom").status_code == 400

    def test_a_method_the_api_does_not_use_is_refused(self, client):
        assert preflight(client, ORIGIN, method="TRACE").status_code == 400


class TestActualRequests:
    def test_a_response_to_the_frontend_origin_carries_the_allow_headers(self, client):
        response = client.get(URL, headers={"Origin": ORIGIN})

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["access-control-allow-credentials"] == "true"

    def test_a_response_to_another_origin_carries_none(self, client):
        response = client.get(URL, headers={"Origin": OTHER})

        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers

    def test_the_allowed_origin_is_never_a_wildcard(self, client):
        response = client.get(URL, headers={"Origin": ORIGIN})

        assert response.headers["access-control-allow-origin"] != "*"
