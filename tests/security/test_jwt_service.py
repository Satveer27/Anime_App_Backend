from uuid import UUID, uuid4

import pytest

from app.exceptions import AuthenticationError
from app.security.exceptions import AlreadyLoggedInError
from app.security.utils.jwt import decode_token, generate_refresh_token


class TestRefreshAccessTokenService:
    async def test_success_rotates_token(self, jwt_service, mock_token_repository, make_refresh_token):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded_original = decode_token(raw_refresh_token, "refresh")
        existing_token = make_refresh_token(user_id=user_id, jti=UUID(decoded_original["jti"]), revoke=False)
        mock_token_repository.get_refresh_token_by_jti.return_value = existing_token

        response = await jwt_service.refresh_access_token_service(raw_refresh_token)

        decoded_new_refresh = decode_token(response.refresh_token, "refresh")
        decoded_new_access = decode_token(response.access_token, "access")
        assert decoded_new_refresh["sub"] == str(user_id)
        assert decoded_new_access["sub"] == str(user_id)
        # rotation inherits the original absolute expiry rather than sliding forward
        assert decoded_new_refresh["exp"] == decoded_original["exp"]

        # old token marked revoked (single-use)
        assert existing_token.revoke is True
        mock_token_repository.update_refresh_token.assert_called_once_with(existing_token)

        # a new row is created for the rotated token. jti/user_id come straight off
        # the decoded JWT payload (plain str/UUID), not through a DB round-trip, so
        # they're compared as the raw types the service actually constructs.
        new_token_arg = mock_token_repository.create_refresh_token.call_args.args[0]
        assert new_token_arg.user_id == user_id
        assert new_token_arg.jti == decoded_new_refresh["jti"]

    async def test_token_not_found_raises(self, jwt_service, mock_token_repository):
        raw_refresh_token = generate_refresh_token(uuid4())
        mock_token_repository.get_refresh_token_by_jti.return_value = None

        with pytest.raises(AuthenticationError):
            await jwt_service.refresh_access_token_service(raw_refresh_token)

        mock_token_repository.update_refresh_token.assert_not_called()

    async def test_reuse_detected_revokes_all_sessions(self, jwt_service, mock_token_repository, mock_redis, make_refresh_token):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded_original = decode_token(raw_refresh_token, "refresh")
        # already revoked once = this jti is being replayed
        existing_token = make_refresh_token(user_id=user_id, jti=UUID(decoded_original["jti"]), revoke=True)
        mock_token_repository.get_refresh_token_by_jti.return_value = existing_token
        mock_redis.zrangebyscore.return_value = []

        with pytest.raises(AuthenticationError):
            await jwt_service.refresh_access_token_service(raw_refresh_token)

        # repository call + redis call args, not just "was revoke called"
        mock_token_repository.delete_all_refresh_tokens_for_user.assert_called_once_with(user_id)
        mock_redis.zrangebyscore.assert_called_once()
        assert mock_redis.zrangebyscore.call_args.args[0] == f"user_tokens:{user_id}"

        mock_token_repository.update_refresh_token.assert_not_called()
        mock_token_repository.create_refresh_token.assert_not_called()


class TestLoginService:
    async def test_success(self, jwt_service, mock_user_repository, mock_token_repository, make_user):
        user = make_user(password="CorrectPass123!", is_verified=True)
        mock_user_repository.get_user_by_email.return_value = user

        response = await jwt_service.login_service(user.email, "CorrectPass123!")

        decoded_refresh = decode_token(response.refresh_token, "refresh")
        decoded_access = decode_token(response.access_token, "access")
        assert decoded_refresh["sub"] == str(user.id)
        assert decoded_access["sub"] == str(user.id)

        new_token_arg = mock_token_repository.create_refresh_token.call_args.args[0]
        assert new_token_arg.user_id == user.id

    async def test_wrong_password_raises(self, jwt_service, mock_user_repository, mock_token_repository, make_user):
        user = make_user(password="CorrectPass123!")
        mock_user_repository.get_user_by_email.return_value = user

        with pytest.raises(AuthenticationError):
            await jwt_service.login_service(user.email, "WrongPass123!")

        mock_token_repository.create_refresh_token.assert_not_called()

    async def test_unverified_email_raises(self, jwt_service, mock_user_repository, mock_token_repository, make_user):
        user = make_user(password="CorrectPass123!", is_verified=False)
        mock_user_repository.get_user_by_email.return_value = user

        with pytest.raises(AuthenticationError):
            await jwt_service.login_service(user.email, "CorrectPass123!")

        mock_token_repository.create_refresh_token.assert_not_called()

    async def test_already_logged_in_blocks_with_valid_refresh_cookie(self, jwt_service, mock_user_repository, mock_token_repository, make_refresh_token):
        existing_user_id = uuid4()
        existing_refresh_token = generate_refresh_token(existing_user_id)
        decoded_existing = decode_token(existing_refresh_token, "refresh")
        existing_token_row = make_refresh_token(user_id=existing_user_id, jti=UUID(decoded_existing["jti"]), revoke=False)
        mock_token_repository.get_refresh_token_by_jti.return_value = existing_token_row

        with pytest.raises(AlreadyLoggedInError):
            await jwt_service.login_service("someone@example.com", "whatever123", refresh_token=existing_refresh_token)

        # short-circuits before credentials are even checked
        mock_user_repository.get_user_by_email.assert_not_called()


class TestLogoutService:
    async def test_success(self, jwt_service, mock_token_repository, make_refresh_token):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded = decode_token(raw_refresh_token, "refresh")
        current_token = make_refresh_token(user_id=user_id, jti=UUID(decoded["jti"]), revoke=False)
        mock_token_repository.get_refresh_token_by_jti.return_value = current_token

        await jwt_service.logout_service(raw_refresh_token)

        assert current_token.revoke is True
        mock_token_repository.update_refresh_token.assert_called_once_with(current_token)

    async def test_token_not_found_raises(self, jwt_service, mock_token_repository):
        raw_refresh_token = generate_refresh_token(uuid4())
        mock_token_repository.get_refresh_token_by_jti.return_value = None

        with pytest.raises(AuthenticationError):
            await jwt_service.logout_service(raw_refresh_token)

        mock_token_repository.update_refresh_token.assert_not_called()

    async def test_already_revoked_token_raises(self, jwt_service, mock_token_repository, make_refresh_token):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded = decode_token(raw_refresh_token, "refresh")
        current_token = make_refresh_token(user_id=user_id, jti=UUID(decoded["jti"]), revoke=True)
        mock_token_repository.get_refresh_token_by_jti.return_value = current_token

        with pytest.raises(AuthenticationError):
            await jwt_service.logout_service(raw_refresh_token)

        mock_token_repository.update_refresh_token.assert_not_called()


class TestLogoutAllAccounts:
    async def test_success_bulk_revokes(self, jwt_service, mock_user_repository, mock_token_repository, mock_redis, make_user, make_refresh_token):
        user = make_user()
        raw_refresh_token = generate_refresh_token(user.id)
        decoded = decode_token(raw_refresh_token, "refresh")
        current_token = make_refresh_token(user_id=user.id, jti=UUID(decoded["jti"]), revoke=False)
        mock_token_repository.get_refresh_token_by_jti.return_value = current_token
        mock_user_repository.get_user_by_id.return_value = user
        mock_token_repository.update_refresh_token_to_revoke.return_value = 3
        mock_redis.zrangebyscore.return_value = []

        await jwt_service.logout_all_accounts(raw_refresh_token)

        # repository call + redis call args, not just "was revoke called"
        mock_token_repository.update_refresh_token_to_revoke.assert_called_once_with(user_id=user.id)
        mock_redis.zrangebyscore.assert_called_once()
        assert mock_redis.zrangebyscore.call_args.args[0] == f"user_tokens:{user.id}"
