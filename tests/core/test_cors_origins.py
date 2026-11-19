import pytest

from app.core.cors import allowed_origins, origin_of


class TestOriginOf:
    @pytest.mark.parametrize(
        ("value", "origin"),
        [
            ("https://app.example.com", "https://app.example.com"),
            ("https://app.example.com/", "https://app.example.com"),
            ("http://localhost:3000", "http://localhost:3000"),
            ("https://app.example.com/some/path?x=1", "https://app.example.com"),
            ("  https://app.example.com  ", "https://app.example.com"),
        ],
    )
    def test_it_keeps_the_scheme_and_host(self, value, origin):
        assert origin_of(value) == origin

    @pytest.mark.parametrize(
        "value", ["*", "https://*.example.com", "app.example.com", "ftp://x.com", "", "https://"]
    )
    def test_a_wildcard_or_a_non_origin_is_refused(self, value):
        with pytest.raises(ValueError):
            origin_of(value)


class TestAllowedOrigins:
    def test_only_the_frontend_by_default(self):
        assert allowed_origins("https://app.example.com") == ["https://app.example.com"]

    def test_extra_origins_are_added_after_it(self):
        result = allowed_origins(
            "https://app.example.com", "http://localhost:3000, http://localhost:5173"
        )

        assert result == [
            "https://app.example.com",
            "http://localhost:3000",
            "http://localhost:5173",
        ]

    def test_a_duplicate_is_listed_once(self):
        result = allowed_origins("http://localhost:3000", "http://localhost:3000/,")

        assert result == ["http://localhost:3000"]

    @pytest.mark.parametrize(
        "extra", ["*", "http://localhost:3000,*", "https://*.example.com", "not an origin"]
    )
    def test_a_wildcard_among_the_extras_is_refused(self, extra):
        with pytest.raises(ValueError):
            allowed_origins("https://app.example.com", extra)

    def test_the_test_environment_allows_only_the_frontend(self):
        from app.config import settings
        from app.core.cors import allowed_origins as parse

        assert parse(settings.frontend_url, settings.cors_extra_origins) == [
            settings.frontend_url.rstrip("/")
        ]
