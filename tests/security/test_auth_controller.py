from app.schemas import SuccessMessage
from app.security.schemas import RefreshResponse, TokenResponse

BASE = "/api/v1/auth"


class TestLoginEndpoint:
    def test_success_sets_refresh_cookie(self, client, fake_jwt_service):
        fake_jwt_service.login_service.return_value = RefreshResponse(
            refresh_token="newrefreshtoken", access_token="newaccesstoken"
        )

        response = client.post(f"{BASE}/login", json={"email": "a@example.com", "password": "Password123!"})

        assert response.status_code == 200
        TokenResponse.model_validate(response.json())
        assert response.json()["access_token"] == "newaccesstoken"

        set_cookie = response.headers.get("set-cookie")
        assert set_cookie is not None
        assert "refresh_token=newrefreshtoken" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Path=/auth" in set_cookie
        assert "SameSite=lax" in set_cookie

        fake_jwt_service.login_service.assert_called_once_with("a@example.com", "Password123!", None)

    def test_malformed_body_returns_422_not_500(self, client, fake_jwt_service):
        response = client.post(f"{BASE}/login", json={"email": "not-an-email", "password": "x"})

        assert response.status_code == 422
        fake_jwt_service.login_service.assert_not_called()

    def test_existing_refresh_cookie_is_forwarded_to_service(self, client, fake_jwt_service):
        fake_jwt_service.login_service.return_value = RefreshResponse(
            refresh_token="newrefreshtoken", access_token="newaccesstoken"
        )

        client.cookies.set("refresh_token", "existingcookie")
        response = client.post(f"{BASE}/login", json={"email": "a@example.com", "password": "Password123!"})

        assert response.status_code == 200
        fake_jwt_service.login_service.assert_called_once_with("a@example.com", "Password123!", "existingcookie")


class TestRefreshEndpoint:
    def test_success_rotates_refresh_cookie(self, client, fake_jwt_service):
        fake_jwt_service.refresh_access_token_service.return_value = RefreshResponse(
            refresh_token="rotatedrefreshtoken", access_token="rotatedaccesstoken"
        )

        client.cookies.set("refresh_token", "oldrefreshtoken")
        response = client.post(f"{BASE}/refresh")

        assert response.status_code == 200
        TokenResponse.model_validate(response.json())
        assert response.json()["access_token"] == "rotatedaccesstoken"

        set_cookie = response.headers.get("set-cookie")
        assert "refresh_token=rotatedrefreshtoken" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Path=/auth" in set_cookie

        fake_jwt_service.refresh_access_token_service.assert_called_once_with("oldrefreshtoken")

    def test_missing_refresh_cookie_returns_401_not_500(self, client, fake_jwt_service):
        response = client.post(f"{BASE}/refresh")

        assert response.status_code == 401
        fake_jwt_service.refresh_access_token_service.assert_not_called()


class TestLogoutEndpoint:
    def test_success_deletes_refresh_cookie(self, client, fake_jwt_service):
        fake_jwt_service.logout_service.return_value = None

        client.cookies.set("refresh_token", "sometoken")
        response = client.post(f"{BASE}/logout", headers={"Authorization": "Bearer someaccesstoken"})

        assert response.status_code == 200
        SuccessMessage.model_validate(response.json())

        set_cookie = response.headers.get("set-cookie")
        assert set_cookie is not None
        assert 'refresh_token=""' in set_cookie
        assert "Max-Age=0" in set_cookie
        assert "Path=/auth" in set_cookie

        fake_jwt_service.logout_service.assert_called_once_with("sometoken", "someaccesstoken")

    def test_success_without_authorization_header(self, client, fake_jwt_service):
        fake_jwt_service.logout_service.return_value = None

        client.cookies.set("refresh_token", "sometoken")
        response = client.post(f"{BASE}/logout")

        assert response.status_code == 200
        fake_jwt_service.logout_service.assert_called_once_with("sometoken", None)

    def test_missing_refresh_cookie_returns_401_not_500(self, client, fake_jwt_service):
        response = client.post(f"{BASE}/logout")

        assert response.status_code == 401
        fake_jwt_service.logout_service.assert_not_called()


class TestLogoutAllEndpoint:
    def test_success_deletes_refresh_cookie(self, client, fake_jwt_service):
        fake_jwt_service.logout_all_accounts.return_value = None

        client.cookies.set("refresh_token", "sometoken")
        response = client.post(f"{BASE}/logout-all")

        assert response.status_code == 200
        SuccessMessage.model_validate(response.json())

        set_cookie = response.headers.get("set-cookie")
        assert set_cookie is not None
        assert 'refresh_token=""' in set_cookie
        assert "Max-Age=0" in set_cookie
        assert "Path=/auth" in set_cookie

        fake_jwt_service.logout_all_accounts.assert_called_once_with("sometoken")

    def test_missing_refresh_cookie_returns_401_not_500(self, client, fake_jwt_service):
        response = client.post(f"{BASE}/logout-all")

        assert response.status_code == 401
        fake_jwt_service.logout_all_accounts.assert_not_called()
