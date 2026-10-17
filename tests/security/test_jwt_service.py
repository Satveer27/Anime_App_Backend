from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import settings
from app.exceptions import AuthenticationError
from app.security.exceptions import AlreadyLoggedInError
from app.security.utils.jwt import decode_token, generate_access_token, generate_refresh_token


class TestRefreshAccessTokenService:
    async def test_success_rotates_token(
        self, jwt_service, mock_token_repository, make_refresh_token
    ):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded_original = decode_token(raw_refresh_token, "refresh")
        existing_token = make_refresh_token(
            user_id=user_id, jti=UUID(decoded_original["jti"]), revoke=False
        )
        mock_token_repository.get_refresh_token_by_jti.return_value = existing_token
        mock_token_repository.revoke_refresh_token.return_value = 1

        response = await jwt_service.refresh_access_token_service(raw_refresh_token)

        decoded_new_refresh = decode_token(response.refresh_token, "refresh")
        decoded_new_access = decode_token(response.access_token, "access")
        assert decoded_new_refresh["sub"] == str(user_id)
        assert decoded_new_access["sub"] == str(user_id)
        # rotation inherits the original absolute expiry rather than sliding forward
        assert decoded_new_refresh["exp"] == decoded_original["exp"]

        # old token revoked via the atomic, jti-scoped update (single-use)
        mock_token_repository.revoke_refresh_token.assert_called_once_with(
            UUID(decoded_original["jti"])
        )

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

        mock_token_repository.revoke_refresh_token.assert_not_called()
        mock_token_repository.create_refresh_token.assert_not_called()

    async def test_reuse_detected_revokes_all_sessions(
        self, jwt_service, mock_token_repository, mock_redis, make_refresh_token
    ):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded_original = decode_token(raw_refresh_token, "refresh")
        # revoked long ago (outside the grace window) = this jti is being replayed
        existing_token = make_refresh_token(
            user_id=user_id,
            jti=UUID(decoded_original["jti"]),
            revoke=True,
            revoked_at=datetime.now(UTC) - timedelta(seconds=settings.refresh_grace_seconds + 60),
        )
        mock_token_repository.get_refresh_token_by_jti.return_value = existing_token
        mock_token_repository.revoke_refresh_token.return_value = 0
        mock_redis.zrangebyscore.return_value = []

        with pytest.raises(AuthenticationError):
            await jwt_service.refresh_access_token_service(raw_refresh_token)

        # repository call + redis call args, not just "was revoke called"
        mock_token_repository.delete_all_refresh_tokens_for_user.assert_called_once_with(user_id)
        mock_redis.zrangebyscore.assert_called_once()
        assert mock_redis.zrangebyscore.call_args.args[0] == f"user_tokens:{user_id}"

        # the session wipe must be committed, since raising AuthenticationError
        # right after would otherwise let get_db's exception handler roll it back
        mock_token_repository.commit.assert_called_once()

        mock_token_repository.create_refresh_token.assert_not_called()

    async def test_reuse_with_no_revoked_at_revokes_all_sessions(
        self, jwt_service, mock_token_repository, mock_redis, make_refresh_token
    ):
        # rows revoked before the revoked_at column existed (or by logout) have NULL
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded_original = decode_token(raw_refresh_token, "refresh")
        existing_token = make_refresh_token(
            user_id=user_id, jti=UUID(decoded_original["jti"]), revoke=True, revoked_at=None
        )
        mock_token_repository.get_refresh_token_by_jti.return_value = existing_token
        mock_token_repository.revoke_refresh_token.return_value = 0
        mock_redis.zrangebyscore.return_value = []

        with pytest.raises(AuthenticationError):
            await jwt_service.refresh_access_token_service(raw_refresh_token)

        mock_token_repository.delete_all_refresh_tokens_for_user.assert_called_once_with(user_id)
        mock_token_repository.create_refresh_token.assert_not_called()

    async def test_racing_refresh_inside_grace_window_does_not_wipe_sessions(
        self, jwt_service, mock_token_repository, mock_redis, make_refresh_token
    ):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded_original = decode_token(raw_refresh_token, "refresh")
        # the winning request revoked this jti a moment ago
        existing_token = make_refresh_token(
            user_id=user_id,
            jti=UUID(decoded_original["jti"]),
            revoke=True,
            revoked_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        mock_token_repository.get_refresh_token_by_jti.return_value = existing_token
        mock_token_repository.revoke_refresh_token.return_value = 0

        with pytest.raises(AuthenticationError):
            await jwt_service.refresh_access_token_service(raw_refresh_token)

        # 401 only: no wipe, no commit, no Redis revoke, and the loser mints nothing
        mock_token_repository.delete_all_refresh_tokens_for_user.assert_not_called()
        mock_token_repository.commit.assert_not_called()
        mock_redis.zrangebyscore.assert_not_called()
        mock_token_repository.create_refresh_token.assert_not_called()

    async def test_token_deleted_during_race_revokes_all_sessions(
        self, jwt_service, mock_token_repository, mock_redis, make_refresh_token
    ):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded_original = decode_token(raw_refresh_token, "refresh")
        existing_token = make_refresh_token(user_id=user_id, jti=UUID(decoded_original["jti"]))
        # first read finds the row, the re-read after losing the race finds it deleted
        mock_token_repository.get_refresh_token_by_jti.side_effect = [existing_token, None]
        mock_token_repository.revoke_refresh_token.return_value = 0
        mock_redis.zrangebyscore.return_value = []

        with pytest.raises(AuthenticationError):
            await jwt_service.refresh_access_token_service(raw_refresh_token)

        mock_token_repository.create_refresh_token.assert_not_called()


class TestLoginService:
    async def test_success(
        self, jwt_service, mock_user_repository, mock_token_repository, make_user
    ):
        user = make_user(password="CorrectPass123!", is_verified=True)
        mock_user_repository.get_user_by_email.return_value = user

        response = await jwt_service.login_service(user.email, "CorrectPass123!")

        decoded_refresh = decode_token(response.refresh_token, "refresh")
        decoded_access = decode_token(response.access_token, "access")
        assert decoded_refresh["sub"] == str(user.id)
        assert decoded_access["sub"] == str(user.id)

        new_token_arg = mock_token_repository.create_refresh_token.call_args.args[0]
        assert new_token_arg.user_id == user.id

    async def test_wrong_password_raises(
        self, jwt_service, mock_user_repository, mock_token_repository, make_user
    ):
        user = make_user(password="CorrectPass123!")
        mock_user_repository.get_user_by_email.return_value = user

        with pytest.raises(AuthenticationError):
            await jwt_service.login_service(user.email, "WrongPass123!")

        mock_token_repository.create_refresh_token.assert_not_called()

    async def test_unverified_email_raises(
        self, jwt_service, mock_user_repository, mock_token_repository, make_user
    ):
        user = make_user(password="CorrectPass123!", is_verified=False)
        mock_user_repository.get_user_by_email.return_value = user

        with pytest.raises(AuthenticationError):
            await jwt_service.login_service(user.email, "CorrectPass123!")

        mock_token_repository.create_refresh_token.assert_not_called()

    async def test_already_logged_in_blocks_with_valid_refresh_cookie(
        self, jwt_service, mock_user_repository, mock_token_repository, make_refresh_token
    ):
        existing_user_id = uuid4()
        existing_refresh_token = generate_refresh_token(existing_user_id)
        decoded_existing = decode_token(existing_refresh_token, "refresh")
        existing_token_row = make_refresh_token(
            user_id=existing_user_id, jti=UUID(decoded_existing["jti"]), revoke=False
        )
        mock_token_repository.get_refresh_token_by_jti.return_value = existing_token_row

        with pytest.raises(AlreadyLoggedInError):
            await jwt_service.login_service(
                "someone@example.com", "whatever123", refresh_token=existing_refresh_token
            )

        # short-circuits before credentials are even checked
        mock_user_repository.get_user_by_email.assert_not_called()


class TestLogoutService:
    async def test_success(self, jwt_service, mock_token_repository, make_refresh_token):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded = decode_token(raw_refresh_token, "refresh")
        current_token = make_refresh_token(user_id=user_id, jti=UUID(decoded["jti"]), revoke=False)
        mock_token_repository.get_refresh_token_by_jti.return_value = current_token

        result = await jwt_service.logout_service(raw_refresh_token)

        assert result is True
        assert current_token.revoke is True
        mock_token_repository.update_refresh_token.assert_called_once_with(current_token)

    async def test_token_not_found_is_idempotent(self, jwt_service, mock_token_repository):
        raw_refresh_token = generate_refresh_token(uuid4())
        mock_token_repository.get_refresh_token_by_jti.return_value = None

        result = await jwt_service.logout_service(raw_refresh_token)

        assert result is False
        mock_token_repository.update_refresh_token.assert_not_called()

    async def test_already_revoked_token_is_idempotent(
        self, jwt_service, mock_token_repository, make_refresh_token
    ):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded = decode_token(raw_refresh_token, "refresh")
        current_token = make_refresh_token(user_id=user_id, jti=UUID(decoded["jti"]), revoke=True)
        mock_token_repository.get_refresh_token_by_jti.return_value = current_token

        result = await jwt_service.logout_service(raw_refresh_token)

        assert result is False
        mock_token_repository.update_refresh_token.assert_not_called()

    async def test_missing_refresh_token_is_idempotent(self, jwt_service, mock_token_repository):
        result = await jwt_service.logout_service(None)

        assert result is False
        mock_token_repository.get_refresh_token_by_jti.assert_not_called()

    async def test_garbage_refresh_token_is_idempotent(self, jwt_service, mock_token_repository):
        result = await jwt_service.logout_service("not-a-jwt")

        assert result is False
        mock_token_repository.get_refresh_token_by_jti.assert_not_called()

    async def test_access_token_revoked_even_when_refresh_already_gone(
        self, jwt_service, mock_token_repository
    ):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        access_token = await generate_access_token(user_id)
        decoded_access = decode_token(access_token, "access")
        mock_token_repository.get_refresh_token_by_jti.return_value = None

        with patch("app.security.service.revoke_single_access_token") as revoke_access:
            result = await jwt_service.logout_service(raw_refresh_token, access_token)

        assert result is False
        revoke_access.assert_awaited_once_with(
            decoded_access["jti"], decoded_access["exp"], user_id
        )


class TestLogoutAllAccounts:
    async def test_success_bulk_revokes(
        self,
        jwt_service,
        mock_user_repository,
        mock_token_repository,
        mock_redis,
        make_user,
        make_refresh_token,
    ):
        user = make_user()
        raw_refresh_token = generate_refresh_token(user.id)
        decoded = decode_token(raw_refresh_token, "refresh")
        current_token = make_refresh_token(user_id=user.id, jti=UUID(decoded["jti"]), revoke=False)
        mock_token_repository.get_refresh_token_by_jti.return_value = current_token
        mock_user_repository.get_user_by_id.return_value = user
        mock_token_repository.update_refresh_token_to_revoke.return_value = 3
        mock_redis.zrangebyscore.return_value = []

        result = await jwt_service.logout_all_accounts(raw_refresh_token)

        assert result is True
        # repository call + redis call args, not just "was revoke called"
        mock_token_repository.update_refresh_token_to_revoke.assert_called_once_with(
            user_id=user.id
        )
        mock_redis.zrangebyscore.assert_called_once()
        assert mock_redis.zrangebyscore.call_args.args[0] == f"user_tokens:{user.id}"

    async def test_token_not_found_is_idempotent(self, jwt_service, mock_token_repository):
        raw_refresh_token = generate_refresh_token(uuid4())
        mock_token_repository.get_refresh_token_by_jti.return_value = None

        result = await jwt_service.logout_all_accounts(raw_refresh_token)

        assert result is False
        mock_token_repository.update_refresh_token_to_revoke.assert_not_called()

    async def test_missing_refresh_token_is_idempotent(self, jwt_service, mock_token_repository):
        result = await jwt_service.logout_all_accounts(None)

        assert result is False
        mock_token_repository.get_refresh_token_by_jti.assert_not_called()

    async def test_garbage_refresh_token_is_idempotent(self, jwt_service, mock_token_repository):
        result = await jwt_service.logout_all_accounts("not-a-jwt")

        assert result is False
        mock_token_repository.get_refresh_token_by_jti.assert_not_called()

    async def test_user_not_found_is_idempotent(
        self,
        jwt_service,
        mock_user_repository,
        mock_token_repository,
        make_refresh_token,
    ):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded = decode_token(raw_refresh_token, "refresh")
        current_token = make_refresh_token(user_id=user_id, jti=UUID(decoded["jti"]), revoke=False)
        mock_token_repository.get_refresh_token_by_jti.return_value = current_token
        mock_user_repository.get_user_by_id.return_value = None

        result = await jwt_service.logout_all_accounts(raw_refresh_token)

        assert result is False
        mock_token_repository.update_refresh_token_to_revoke.assert_not_called()

    async def test_already_revoked_token_is_idempotent(
        self, jwt_service, mock_token_repository, mock_redis, make_refresh_token
    ):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded = decode_token(raw_refresh_token, "refresh")
        current_token = make_refresh_token(user_id=user_id, jti=UUID(decoded["jti"]), revoke=True)
        mock_token_repository.get_refresh_token_by_jti.return_value = current_token

        result = await jwt_service.logout_all_accounts(raw_refresh_token)

        assert result is False
        mock_token_repository.update_refresh_token_to_revoke.assert_not_called()
        mock_redis.zrangebyscore.assert_not_called()


class TestRedisOutageFallbacks:
    async def test_logout_still_revokes_refresh_token_when_redis_is_down(
        self, jwt_service, mock_token_repository, mock_redis, make_refresh_token
    ):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        raw_access_token = await generate_access_token(user_id)
        decoded = decode_token(raw_refresh_token, "refresh")
        current_token = make_refresh_token(user_id=user_id, jti=UUID(decoded["jti"]), revoke=False)
        mock_token_repository.get_refresh_token_by_jti.return_value = current_token
        mock_redis.setex.side_effect = RedisConnectionError("boom")

        result = await jwt_service.logout_service(raw_refresh_token, raw_access_token)

        assert result is True
        assert current_token.revoke is True
        mock_token_repository.update_refresh_token.assert_called_once_with(current_token)

    async def test_reuse_detection_still_returns_401_when_redis_is_down(
        self, jwt_service, mock_token_repository, mock_redis, make_refresh_token
    ):
        user_id = uuid4()
        raw_refresh_token = generate_refresh_token(user_id)
        decoded = decode_token(raw_refresh_token, "refresh")
        mock_token_repository.get_refresh_token_by_jti.return_value = make_refresh_token(
            user_id=user_id,
            jti=UUID(decoded["jti"]),
            revoke=True,
            revoked_at=datetime.now(UTC) - timedelta(seconds=settings.refresh_grace_seconds + 60),
        )
        mock_token_repository.revoke_refresh_token.return_value = 0
        mock_redis.zremrangebyscore.side_effect = RedisConnectionError("boom")

        with pytest.raises(AuthenticationError):
            await jwt_service.refresh_access_token_service(raw_refresh_token)

        mock_token_repository.delete_all_refresh_tokens_for_user.assert_called_once_with(user_id)
        mock_token_repository.commit.assert_called_once()

    async def test_login_surfaces_redis_error_for_503_mapping(
        self, jwt_service, mock_user_repository, mock_redis, make_user
    ):
        user = make_user(password="Password123!")
        mock_user_repository.get_user_by_email.return_value = user
        mock_redis.zadd.side_effect = RedisConnectionError("boom")

        with pytest.raises(RedisConnectionError):
            await jwt_service.login_service(user.email, "Password123!")


class TestPasswordCheckOffEventLoop:
    async def test_login_checks_password_in_a_worker_thread(
        self, jwt_service, mock_user_repository, mocker, make_user
    ):
        import threading

        seen: list[int] = []

        def fake_check(plain, hashed):
            seen.append(threading.get_ident())
            return True

        mocker.patch("app.security.service.check_password", side_effect=fake_check)
        user = make_user()
        mock_user_repository.get_user_by_email.return_value = user

        await jwt_service.login_service(user.email, "whatever")

        assert seen and seen[0] != threading.get_ident()
