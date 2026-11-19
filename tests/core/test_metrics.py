def scrape(client) -> str:
    response = client.get("/metrics")
    assert response.status_code == 200
    return response.text


class TestMetricsEndpoint:
    def test_it_exposes_prometheus_text(self, client):
        client.get("/api/v1/anime/not-a-number/statistics")  # a 422, handled without a database

        body = scrape(client)

        assert "http_requests_total" in body
        assert "http_request_duration_seconds_bucket" in body

    def test_a_series_is_one_route_template_and_not_one_per_url(self, client):
        client.get("/api/v1/anime/not-a-number/statistics")
        client.get("/api/v1/anime/also-not-a-number/statistics")

        body = scrape(client)

        assert 'handler="/api/v1/anime/{mal_id}/statistics"' in body
        assert "not-a-number" not in body and "also-not-a-number" not in body

    def test_the_status_is_grouped_by_class(self, client):
        client.get("/api/v1/anime/not-a-number/statistics")

        assert 'status="4xx"' in scrape(client)

    def test_the_health_checks_and_metrics_are_not_counted(self, client):
        client.get("/api/v1/health/live")
        client.get("/metrics")

        body = scrape(client)

        assert 'handler="/api/v1/health/live"' not in body
        assert 'handler="/metrics"' not in body

    def test_it_is_not_in_the_public_api_documentation(self, client):
        assert "/metrics" not in client.get("/openapi.json").json()["paths"]

    def test_the_cache_counter_is_exposed(self, client):
        from app.core.cache import CACHE_REQUESTS

        CACHE_REQUESTS.labels("metrics_test", "hit").inc()

        assert 'cache_requests_total{family="metrics_test",result="hit"}' in scrape(client)
