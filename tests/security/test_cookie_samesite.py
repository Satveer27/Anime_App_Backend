import pytest

from app.config import settings
from app.security.schemas import RefreshResponse

BASE = "/api/v1/auth"


@pytest.fixture
def cross_site(mocker):
    """The production setting: a frontend on another site (localhost, Vercel) keeps the cookie."""
    mocker.patch.object(settings, "cookie_samesite", "none")
    mocker.patch.object(settings, "http_secure", True)


def login(client, fake_jwt_service):
    fake_jwt_service.login_service.return_value = RefreshResponse(
        refresh_token="newrefreshtoken", access_token="newaccesstoken"
    )
    return client.post(f"{BASE}/login", json={"email": "a@example.com", "password": "Password123!"})


class TestDefaults:
    def test_the_default_is_lax_so_local_development_needs_no_https(self):
        assert settings.cookie_samesite == "lax"
        assert settings.http_secure is False


class TestCrossSiteCookie:
    def test_login_sets_samesite_none_with_secure_and_httponly(
        self, client, fake_jwt_service, cross_site
    ):
        set_cookie = login(client, fake_jwt_service).headers["set-cookie"]

        assert "SameSite=none" in set_cookie
        assert "Secure" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Path=/api/v1/auth" in set_cookie

    def test_refresh_keeps_the_same_attributes(self, client, fake_jwt_service, cross_site):
        fake_jwt_service.refresh_access_token_service.return_value = RefreshResponse(
            refresh_token="rotated", access_token="newaccesstoken"
        )
        client.cookies.set("refresh_token", "old")

        set_cookie = client.post(f"{BASE}/refresh").headers["set-cookie"]

        assert "refresh_token=rotated" in set_cookie
        assert "SameSite=none" in set_cookie and "Secure" in set_cookie

    @pytest.mark.parametrize("endpoint", ["logout", "logout-all"])
    def test_clearing_the_cookie_matches_it(self, client, fake_jwt_service, cross_site, endpoint):
        fake_jwt_service.logout_service.return_value = True
        fake_jwt_service.logout_all_accounts.return_value = True
        client.cookies.set("refresh_token", "sometoken")

        response = client.post(f"{BASE}/{endpoint}", headers={"Authorization": "Bearer access"})

        set_cookie = response.headers["set-cookie"]
        assert "Max-Age=0" in set_cookie
        assert "SameSite=none" in set_cookie and "Secure" in set_cookie
