from datetime import UTC, datetime
from uuid import uuid4

import pytest
from kombu.exceptions import OperationalError
from sqlalchemy.exc import IntegrityError

from app.exceptions import (
    AuthenticationError,
    ForbiddenError,
    ResourceDoesNotExistError,
    ServiceUnavailableError,
    TooManyRequestsError,
)
from app.security.utils.password import check_password
from app.users.exceptions import UserAlreadyExistsError
from app.users.schemas import GetAllUserSchema, UserCreateSchema, UserUpdateSchema


class TestCreateUserService:
    async def test_success(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        from app.users.schemas import UserCreateSchema

        mock_user_repository.get_user_by_email.return_value = None
        created_user = make_user(email="new@example.com", username="newuser")
        mock_user_repository.create_user.return_value = created_user

        payload = UserCreateSchema(
            email="new@example.com", password="Password123!", username="newuser"
        )
        response = await user_service.create_user_service(payload)

        assert response.email == "new@example.com"
        assert response.username == "newuser"

        assert mock_redis.setex.call_count == 2
        token_call, mapping_call = mock_redis.setex.call_args_list
        assert token_call.args[0].startswith("email_verification:")
        assert token_call.args[1] == 900
        assert token_call.args[2] == str(created_user.id)
        assert mapping_call.args[0] == f"email_verification_user:{created_user.id}"
        assert mapping_call.args[1] == 900

        celery_call = mock_celery_tasks["verification"].call_args
        assert celery_call.args[0] == created_user.email
        # same random token flows into the redis keys and the task call
        assert token_call.args[0] == f"email_verification:{celery_call.args[1]}"
        assert mapping_call.args[2] == celery_call.args[1]

    async def test_duplicate_email_raises(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        from app.users.schemas import UserCreateSchema

        mock_user_repository.get_user_by_email.return_value = make_user(email="taken@example.com")

        payload = UserCreateSchema(
            email="taken@example.com", password="Password123!", username="newuser"
        )
        with pytest.raises(UserAlreadyExistsError):
            await user_service.create_user_service(payload)

        mock_user_repository.create_user.assert_not_called()
        mock_redis.setex.assert_not_called()
        mock_celery_tasks["verification"].assert_not_called()

    async def test_email_taken_in_race_raises_already_exists(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks
    ):
        from app.users.schemas import UserCreateSchema

        # the pre-check passes, but a concurrent signup wins the unique constraint
        mock_user_repository.get_user_by_email.return_value = None
        mock_user_repository.create_user.side_effect = IntegrityError("stmt", {}, Exception())

        payload = UserCreateSchema(
            email="taken@example.com", password="Password123!", username="newuser"
        )
        with pytest.raises(UserAlreadyExistsError):
            await user_service.create_user_service(payload)

        mock_redis.setex.assert_not_called()
        mock_celery_tasks["verification"].assert_not_called()

    async def test_commits_before_redis_and_email(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        from app.users.schemas import UserCreateSchema

        mock_user_repository.get_user_by_email.return_value = None
        mock_user_repository.create_user.return_value = make_user(email="new@example.com")
        events: list[str] = []
        mock_user_repository.commit.side_effect = lambda: events.append("commit")
        mock_redis.setex.side_effect = lambda *a, **k: events.append("setex")
        mock_celery_tasks["verification"].side_effect = lambda *a, **k: events.append("delay")

        payload = UserCreateSchema(
            email="new@example.com", password="Password123!", username="newuser"
        )
        await user_service.create_user_service(payload)

        assert events[0] == "commit"
        assert set(events[1:]) == {"setex", "delay"}

    async def test_commit_failure_writes_no_tokens_and_sends_no_email(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        from app.users.schemas import UserCreateSchema

        mock_user_repository.get_user_by_email.return_value = None
        mock_user_repository.create_user.return_value = make_user(email="new@example.com")
        mock_user_repository.commit.side_effect = RuntimeError("db down")

        payload = UserCreateSchema(
            email="new@example.com", password="Password123!", username="newuser"
        )
        with pytest.raises(RuntimeError):
            await user_service.create_user_service(payload)

        mock_redis.setex.assert_not_called()
        mock_celery_tasks["verification"].assert_not_called()


class TestVerifyUserEmailService:
    async def test_success(self, user_service, mock_user_repository, mock_redis, make_user):
        user = make_user(is_verified=False)
        mock_redis.getdel.return_value = str(user.id)
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.update_user.return_value = user

        result = await user_service.verify_user_email_service("sometoken")

        assert result.success_message == "Email verified successfully."
        assert user.is_verified is True
        mock_user_repository.update_user.assert_called_once_with(user)
        # the token is consumed atomically on read, so it is single-use
        mock_redis.getdel.assert_called_once_with("email_verification:sometoken")
        mock_redis.delete.assert_called_once_with(f"email_verification_user:{user.id}")

    async def test_token_not_found(self, user_service, mock_user_repository, mock_redis):
        mock_redis.getdel.return_value = None

        with pytest.raises(ResourceDoesNotExistError):
            await user_service.verify_user_email_service("badtoken")

        mock_user_repository.get_user_by_id.assert_not_called()

    async def test_db_failure_after_token_consumed_leaves_user_unverified(
        self, user_service, mock_user_repository, mock_redis, make_user
    ):
        user = make_user(is_verified=False)
        mock_redis.getdel.return_value = str(user.id)
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.update_user.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await user_service.verify_user_email_service("sometoken")

        # the user's mapping key is kept so the resend flow can still replace it
        mock_redis.delete.assert_not_called()

    async def test_user_not_found(self, user_service, mock_user_repository, mock_redis):
        mock_redis.getdel.return_value = str(uuid4())
        mock_user_repository.get_user_by_id.return_value = None

        with pytest.raises(ResourceDoesNotExistError):
            await user_service.verify_user_email_service("sometoken")

        mock_user_repository.update_user.assert_not_called()


class TestResendEmailVerificationService:
    async def test_success(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        user = make_user(is_verified=False)
        mock_user_repository.get_user_by_email.return_value = user
        mock_redis.set.return_value = True
        mock_redis.get.return_value = None

        result = await user_service.resend_email_verification_service(user.email)

        assert "If the email exists" in result.success_message
        assert mock_redis.setex.call_count == 2
        mock_redis.delete.assert_not_called()
        mock_celery_tasks["verification"].assert_called_once()
        assert mock_celery_tasks["verification"].call_args.args[0] == user.email

    async def test_invalidates_previous_token(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        user = make_user(is_verified=False)
        mock_user_repository.get_user_by_email.return_value = user
        mock_redis.set.return_value = True
        mock_redis.get.return_value = "old-token"

        await user_service.resend_email_verification_service(user.email)

        mock_redis.delete.assert_called_once_with("email_verification:old-token")

    async def test_user_not_found_returns_generic_message(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks
    ):
        mock_user_repository.get_user_by_email.return_value = None

        result = await user_service.resend_email_verification_service("nobody@example.com")

        assert "If the email exists" in result.success_message
        mock_redis.get.assert_not_called()
        mock_celery_tasks["verification"].assert_not_called()

    async def test_already_verified_returns_generic_message(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        user = make_user(is_verified=True)
        mock_user_repository.get_user_by_email.return_value = user

        result = await user_service.resend_email_verification_service(user.email)

        assert "If the email exists" in result.success_message
        mock_redis.get.assert_not_called()
        mock_celery_tasks["verification"].assert_not_called()

    async def test_rate_limited(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        user = make_user(is_verified=False)
        mock_user_repository.get_user_by_email.return_value = user
        mock_redis.set.return_value = None

        with pytest.raises(TooManyRequestsError):
            await user_service.resend_email_verification_service(user.email)

        # the cooldown is taken atomically (NX), and a loser sends and writes nothing
        mock_redis.set.assert_called_once_with(f"resend_cooldown:{user.id}", "1", nx=True, ex=300)
        mock_redis.setex.assert_not_called()
        mock_celery_tasks["verification"].assert_not_called()

    async def test_enqueue_failure_releases_cooldown(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        user = make_user(is_verified=False)
        mock_user_repository.get_user_by_email.return_value = user
        mock_redis.set.return_value = True
        mock_redis.get.return_value = None
        mock_celery_tasks["verification"].side_effect = OperationalError("broker down")

        with pytest.raises(ServiceUnavailableError):
            await user_service.resend_email_verification_service(user.email)

        # nothing was queued, so the user can retry immediately
        mock_redis.delete.assert_called_once_with(f"resend_cooldown:{user.id}")


class TestForgotPasswordService:
    async def test_success(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        user = make_user()
        mock_user_repository.get_user_by_email.return_value = user
        mock_redis.set.return_value = True
        mock_redis.get.return_value = None

        result = await user_service.forgot_password_service(user.email)

        assert "If the email exists" in result.success_message
        assert mock_redis.setex.call_count == 2
        mock_redis.delete.assert_not_called()
        mock_celery_tasks["password_reset"].assert_called_once()
        assert mock_celery_tasks["password_reset"].call_args.args[0] == user.email

    async def test_invalidates_previous_token(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        user = make_user()
        mock_user_repository.get_user_by_email.return_value = user
        mock_redis.set.return_value = True
        mock_redis.get.return_value = "old-token"

        await user_service.forgot_password_service(user.email)

        mock_redis.delete.assert_called_once_with("password_reset:old-token")

    async def test_user_not_found_returns_generic_message(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks
    ):
        mock_user_repository.get_user_by_email.return_value = None

        result = await user_service.forgot_password_service("nobody@example.com")

        assert "If the email exists" in result.success_message
        mock_redis.get.assert_not_called()
        mock_celery_tasks["password_reset"].assert_not_called()

    async def test_rate_limited(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        user = make_user()
        mock_user_repository.get_user_by_email.return_value = user
        mock_redis.set.return_value = None

        with pytest.raises(TooManyRequestsError):
            await user_service.forgot_password_service(user.email)

        # the losing caller must not touch the winner's token or send a second email
        mock_redis.set.assert_called_once_with(
            f"forgot_password_cooldown:{user.id}", "1", nx=True, ex=300
        )
        mock_redis.delete.assert_not_called()
        mock_redis.setex.assert_not_called()
        mock_celery_tasks["password_reset"].assert_not_called()

    async def test_enqueue_failure_releases_cooldown(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        user = make_user()
        mock_user_repository.get_user_by_email.return_value = user
        mock_redis.set.return_value = True
        mock_redis.get.return_value = None
        mock_celery_tasks["password_reset"].side_effect = OperationalError("broker down")

        with pytest.raises(ServiceUnavailableError):
            await user_service.forgot_password_service(user.email)

        mock_redis.delete.assert_called_once_with(f"forgot_password_cooldown:{user.id}")


class TestResetPasswordServiceViaEmail:
    async def test_success_revokes_sessions(
        self, user_service, mock_user_repository, mock_token_repository, mock_redis, make_user
    ):
        user = make_user()
        old_password_hash = user.password
        mock_redis.getdel.return_value = str(user.id)
        mock_redis.zrangebyscore.return_value = []
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.update_user.return_value = user

        result = await user_service.reset_password_service_via_email(
            "sometoken", "BrandNewPass123!"
        )

        assert result.success_message == "Password reset successfully."
        assert user.password != old_password_hash
        assert check_password("BrandNewPass123!", user.password)
        # the token is consumed atomically on read, so it is single-use
        mock_redis.getdel.assert_called_once_with("password_reset:sometoken")
        mock_redis.delete.assert_called_once_with(f"password_reset_user:{user.id}")

        # sessions revoked: refresh tokens deleted + access tokens revoked in redis
        mock_token_repository.delete_all_refresh_tokens_for_user.assert_called_once_with(user.id)
        mock_redis.zrangebyscore.assert_called_once()
        assert mock_redis.zrangebyscore.call_args.args[0] == f"user_tokens:{user.id}"

    async def test_token_not_found(self, user_service, mock_user_repository, mock_redis):
        mock_redis.getdel.return_value = None

        with pytest.raises(ResourceDoesNotExistError):
            await user_service.reset_password_service_via_email("badtoken", "NewPass123!")

        mock_user_repository.get_user_by_id.assert_not_called()

    async def test_user_not_found(self, user_service, mock_user_repository, mock_redis):
        mock_redis.getdel.return_value = str(uuid4())
        mock_user_repository.get_user_by_id.return_value = None

        with pytest.raises(ResourceDoesNotExistError):
            await user_service.reset_password_service_via_email("sometoken", "NewPass123!")


class TestUpdateEmailService:
    async def test_success(
        self,
        user_service,
        mock_user_repository,
        mock_token_repository,
        mock_redis,
        mock_celery_tasks,
        make_user,
    ):
        user = make_user(email="old@example.com", is_verified=True)
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.get_user_by_email.return_value = None
        mock_user_repository.update_user.return_value = user
        mock_redis.zrangebyscore.return_value = []
        mock_redis.get.return_value = None

        result = await user_service.update_email_service(user.id, "new@example.com")

        assert "updated" in result.success_message
        assert user.email == "new@example.com"
        assert user.is_verified is False
        mock_token_repository.delete_all_refresh_tokens_for_user.assert_called_once_with(user.id)
        mock_celery_tasks["verification"].assert_called_once()
        mock_redis.delete.assert_not_called()

    async def test_invalidates_previous_verification_token(
        self,
        user_service,
        mock_user_repository,
        mock_token_repository,
        mock_redis,
        mock_celery_tasks,
        make_user,
    ):
        user = make_user(email="old@example.com", is_verified=False)
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.get_user_by_email.return_value = None
        mock_user_repository.update_user.return_value = user
        mock_redis.zrangebyscore.return_value = []
        mock_redis.get.return_value = "old-token"

        await user_service.update_email_service(user.id, "new@example.com")

        mock_redis.delete.assert_any_call("email_verification:old-token")

    async def test_same_email_is_noop(
        self,
        user_service,
        mock_user_repository,
        mock_token_repository,
        mock_celery_tasks,
        make_user,
    ):
        user = make_user(email="same@example.com")
        mock_user_repository.get_user_by_id.return_value = user

        result = await user_service.update_email_service(user.id, "same@example.com")

        assert "already your current email" in result.success_message
        mock_user_repository.update_user.assert_not_called()
        mock_token_repository.delete_all_refresh_tokens_for_user.assert_not_called()
        mock_celery_tasks["verification"].assert_not_called()

    async def test_email_already_taken_raises(self, user_service, mock_user_repository, make_user):
        user = make_user(email="old@example.com")
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.get_user_by_email.return_value = make_user(email="new@example.com")

        with pytest.raises(UserAlreadyExistsError):
            await user_service.update_email_service(user.id, "new@example.com")

        mock_user_repository.update_user.assert_not_called()

    async def test_email_taken_in_race_raises_already_exists(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        user = make_user(email="old@example.com")
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.get_user_by_email.return_value = None
        mock_user_repository.update_user.side_effect = IntegrityError("stmt", {}, Exception())

        with pytest.raises(UserAlreadyExistsError):
            await user_service.update_email_service(user.id, "new@example.com")

        mock_redis.setex.assert_not_called()
        mock_celery_tasks["verification"].assert_not_called()

    async def test_user_not_found(self, user_service, mock_user_repository):
        mock_user_repository.get_user_by_id.return_value = None

        with pytest.raises(ResourceDoesNotExistError):
            await user_service.update_email_service(uuid4(), "new@example.com")

    async def test_commits_before_redis_and_email(
        self,
        user_service,
        mock_user_repository,
        mock_token_repository,
        mock_redis,
        mock_celery_tasks,
        make_user,
    ):
        user = make_user(email="old@example.com")
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.get_user_by_email.return_value = None
        mock_redis.get.return_value = None
        mock_redis.zrangebyscore.return_value = []
        events: list[str] = []
        mock_user_repository.commit.side_effect = lambda: events.append("commit")
        mock_redis.setex.side_effect = lambda *a, **k: events.append("setex")
        mock_celery_tasks["verification"].side_effect = lambda *a, **k: events.append("delay")

        await user_service.update_email_service(user.id, "new@example.com")

        assert events[0] == "commit"
        assert set(events[1:]) == {"setex", "delay"}
        mock_token_repository.delete_all_refresh_tokens_for_user.assert_called_once_with(user.id)

    async def test_commit_failure_writes_no_tokens_and_sends_no_email(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        user = make_user(email="old@example.com")
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.get_user_by_email.return_value = None
        mock_user_repository.commit.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await user_service.update_email_service(user.id, "new@example.com")

        mock_redis.setex.assert_not_called()
        mock_celery_tasks["verification"].assert_not_called()


class TestUpdateUserFieldsService:
    async def test_success_updates_username(self, user_service, mock_user_repository, make_user):
        user = make_user(username="oldname")
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.update_user.return_value = user

        response = await user_service.update_user_fields_service(
            UserUpdateSchema(username="newname"), user.id
        )

        assert response.username == "newname"
        assert user.username == "newname"
        mock_user_repository.update_user.assert_called_once_with(user)

    async def test_noop_when_username_is_none(self, user_service, mock_user_repository, make_user):
        user = make_user(username="unchanged")
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.update_user.return_value = user

        response = await user_service.update_user_fields_service(
            UserUpdateSchema(username=None), user.id
        )

        assert response.username == "unchanged"
        mock_user_repository.update_user.assert_called_once_with(user)

    async def test_user_not_found(self, user_service, mock_user_repository):
        mock_user_repository.get_user_by_id.return_value = None

        with pytest.raises(ResourceDoesNotExistError):
            await user_service.update_user_fields_service(
                UserUpdateSchema(username="x" * 5), uuid4()
            )


class TestDeleteCurrentUserService:
    async def test_success(self, user_service, mock_user_repository, mock_redis, make_user):
        user = make_user()
        mock_user_repository.get_user_by_id.return_value = user
        mock_redis.zrangebyscore.return_value = []

        result = await user_service.delete_current_user_service(user.id)

        assert result.success_message == "User deleted successfully."
        mock_user_repository.delete_user.assert_called_once_with(user)
        mock_redis.zrangebyscore.assert_called_once()
        assert mock_redis.zrangebyscore.call_args.args[0] == f"user_tokens:{user.id}"

    async def test_user_not_found(self, user_service, mock_user_repository):
        mock_user_repository.get_user_by_id.return_value = None

        with pytest.raises(ResourceDoesNotExistError):
            await user_service.delete_current_user_service(uuid4())

        mock_user_repository.delete_user.assert_not_called()

    async def test_admin_self_delete_blocked(self, user_service, mock_user_repository, make_user):
        user = make_user(is_admin=True)
        mock_user_repository.get_user_by_id.return_value = user

        with pytest.raises(ForbiddenError):
            await user_service.delete_current_user_service(user.id)

        mock_user_repository.delete_user.assert_not_called()

    async def test_self_delete_revokes_tokens_before_deleting_account(
        self, user_service, mock_user_repository, mock_token_repository, mocker, make_user
    ):
        user = make_user(is_admin=False)
        mock_user_repository.get_user_by_id.return_value = user
        events: list[str] = []
        mock_token_repository.delete_all_refresh_tokens_for_user.side_effect = lambda uid: (
            events.append("sessions")
        )
        mock_user_repository.delete_user.side_effect = lambda u: events.append("delete")
        mock_user_repository.commit.side_effect = lambda: events.append("commit")
        mocker.patch(
            "app.users.service.revoke_access_to_all_tokens",
            side_effect=lambda uid: events.append("revoke"),
        )

        await user_service.delete_current_user_service(user.id)

        assert events == ["revoke", "sessions", "delete", "commit"]

    async def test_self_delete_redis_failure_deletes_nothing(
        self, user_service, mock_user_repository, mock_token_repository, mocker, make_user
    ):
        from redis.exceptions import ConnectionError as RedisConnectionError

        user = make_user(is_admin=False)
        mock_user_repository.get_user_by_id.return_value = user
        mocker.patch(
            "app.users.service.revoke_access_to_all_tokens",
            side_effect=RedisConnectionError("boom"),
        )

        with pytest.raises(RedisConnectionError):
            await user_service.delete_current_user_service(user.id)

        mock_token_repository.delete_all_refresh_tokens_for_user.assert_not_called()
        mock_user_repository.delete_user.assert_not_called()
        mock_user_repository.commit.assert_not_called()


class TestUpdateUserPasswordService:
    async def test_success_revokes_sessions(
        self, user_service, mock_user_repository, mock_token_repository, mock_redis, make_user
    ):
        user = make_user(password="OldPass123!")
        old_password_hash = user.password
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.update_user.return_value = user
        mock_redis.zrangebyscore.return_value = []

        result = await user_service.update_user_password_service(
            user.id, "NewPass123!", "OldPass123!"
        )

        assert result.success_message == "Password updated successfully."
        assert user.password != old_password_hash
        assert check_password("NewPass123!", user.password)
        mock_token_repository.delete_all_refresh_tokens_for_user.assert_called_once_with(user.id)
        mock_redis.zrangebyscore.assert_called_once()

    async def test_wrong_old_password_raises(self, user_service, mock_user_repository, make_user):
        user = make_user(password="OldPass123!")
        mock_user_repository.get_user_by_id.return_value = user

        with pytest.raises(AuthenticationError):
            await user_service.update_user_password_service(user.id, "NewPass123!", "WrongPass123!")

        mock_user_repository.update_user.assert_not_called()

    async def test_user_not_found(self, user_service, mock_user_repository):
        mock_user_repository.get_user_by_id.return_value = None

        with pytest.raises(ResourceDoesNotExistError):
            await user_service.update_user_password_service(uuid4(), "NewPass123!", "OldPass123!")


class TestGetAllUsersService:
    async def test_filters_are_passed_through(self, user_service, mock_user_repository, make_user):
        created_after = datetime(2024, 1, 1, tzinfo=UTC)
        created_before = datetime(2024, 12, 31, tzinfo=UTC)
        request = GetAllUserSchema(
            username="ann",
            email=None,
            is_admin=True,
            created_after=created_after,
            created_before=created_before,
            username_sorted_bool=True,
        )
        mock_user_repository.count_users.return_value = 2
        mock_user_repository.get_users.return_value = [make_user(), make_user()]

        result = await user_service.get_all_users_service(
            request, page=2, page_size=10, admin_id=uuid4()
        )

        assert result.total == 2
        assert result.page == 2
        assert result.page_size == 10
        assert len(result.items) == 2

        mock_user_repository.count_users.assert_called_once_with(
            username="ann",
            email=None,
            is_admin=True,
            created_after=created_after,
            created_before=created_before,
        )
        mock_user_repository.get_users.assert_called_once_with(
            limit=10,
            offset=10,  # (page - 1) * page_size
            username="ann",
            email=None,
            is_admin=True,
            created_after=created_after,
            created_before=created_before,
            username_sorted_bool=True,
        )


class TestUpdateUserEmailByIdService:
    async def test_success(
        self,
        user_service,
        mock_user_repository,
        mock_token_repository,
        mock_redis,
        mock_celery_tasks,
        make_user,
    ):
        user = make_user(email="old@example.com", is_verified=True)
        admin_id = uuid4()
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.get_user_by_email.return_value = None
        mock_user_repository.update_user.return_value = user
        mock_redis.zrangebyscore.return_value = []
        mock_redis.get.return_value = None

        response = await user_service.update_user_email_by_id_service(
            user.id, "new@example.com", admin_id
        )

        assert response.email == "new@example.com"
        assert user.is_verified is False
        mock_token_repository.delete_all_refresh_tokens_for_user.assert_called_once_with(user.id)
        mock_celery_tasks["verification"].assert_called_once()
        mock_redis.delete.assert_not_called()

    async def test_invalidates_previous_verification_token(
        self,
        user_service,
        mock_user_repository,
        mock_token_repository,
        mock_redis,
        mock_celery_tasks,
        make_user,
    ):
        user = make_user(email="old@example.com", is_verified=False)
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.get_user_by_email.return_value = None
        mock_user_repository.update_user.return_value = user
        mock_redis.zrangebyscore.return_value = []
        mock_redis.get.return_value = "old-token"

        await user_service.update_user_email_by_id_service(user.id, "new@example.com", uuid4())

        mock_redis.delete.assert_any_call("email_verification:old-token")

    async def test_email_already_taken_raises(self, user_service, mock_user_repository, make_user):
        user = make_user(email="old@example.com")
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.get_user_by_email.return_value = make_user(email="new@example.com")

        with pytest.raises(UserAlreadyExistsError):
            await user_service.update_user_email_by_id_service(user.id, "new@example.com", uuid4())

    async def test_email_taken_in_race_raises_already_exists(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        user = make_user(email="old@example.com")
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.get_user_by_email.return_value = None
        mock_user_repository.update_user.side_effect = IntegrityError("stmt", {}, Exception())

        with pytest.raises(UserAlreadyExistsError):
            await user_service.update_user_email_by_id_service(user.id, "new@example.com", uuid4())

        mock_redis.setex.assert_not_called()
        mock_celery_tasks["verification"].assert_not_called()

    async def test_user_not_found(self, user_service, mock_user_repository):
        mock_user_repository.get_user_by_id.return_value = None

        with pytest.raises(ResourceDoesNotExistError):
            await user_service.update_user_email_by_id_service(uuid4(), "new@example.com", uuid4())

    async def test_commits_before_redis_and_email(
        self,
        user_service,
        mock_user_repository,
        mock_token_repository,
        mock_redis,
        mock_celery_tasks,
        make_user,
    ):
        user = make_user(email="old@example.com")
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.get_user_by_email.return_value = None
        mock_redis.get.return_value = None
        mock_redis.zrangebyscore.return_value = []
        mock_user_repository.update_user.return_value = user
        events: list[str] = []
        mock_user_repository.commit.side_effect = lambda: events.append("commit")
        mock_redis.setex.side_effect = lambda *a, **k: events.append("setex")
        mock_celery_tasks["verification"].side_effect = lambda *a, **k: events.append("delay")

        await user_service.update_user_email_by_id_service(user.id, "new@example.com", uuid4())

        assert events[0] == "commit"
        assert set(events[1:]) == {"setex", "delay"}
        mock_token_repository.delete_all_refresh_tokens_for_user.assert_called_once_with(user.id)

    async def test_commit_failure_writes_no_tokens_and_sends_no_email(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        user = make_user(email="old@example.com")
        mock_user_repository.get_user_by_id.return_value = user
        mock_user_repository.get_user_by_email.return_value = None
        mock_user_repository.commit.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await user_service.update_user_email_by_id_service(user.id, "new@example.com", uuid4())

        mock_redis.setex.assert_not_called()
        mock_celery_tasks["verification"].assert_not_called()


class TestDeleteUserByIdService:
    async def test_success(self, user_service, mock_user_repository, mock_redis, make_user):
        user = make_user(is_admin=False)
        mock_user_repository.get_user_by_id.return_value = user
        mock_redis.zrangebyscore.return_value = []

        result = await user_service.delete_user_by_id_service(user.id, admin_id=uuid4())

        assert result.success_message == "User deleted successfully."
        mock_user_repository.delete_user.assert_called_once_with(user)

    async def test_self_delete_blocked(self, user_service, mock_user_repository):
        admin_id = uuid4()

        with pytest.raises(ForbiddenError):
            await user_service.delete_user_by_id_service(admin_id, admin_id=admin_id)

        mock_user_repository.get_user_by_id.assert_not_called()

    async def test_delete_other_admin_blocked(self, user_service, mock_user_repository, make_user):
        target_admin = make_user(is_admin=True)
        mock_user_repository.get_user_by_id.return_value = target_admin

        with pytest.raises(ForbiddenError):
            await user_service.delete_user_by_id_service(target_admin.id, admin_id=uuid4())

        mock_user_repository.delete_user.assert_not_called()

    async def test_user_not_found(self, user_service, mock_user_repository):
        mock_user_repository.get_user_by_id.return_value = None

        with pytest.raises(ResourceDoesNotExistError):
            await user_service.delete_user_by_id_service(uuid4(), admin_id=uuid4())

    async def test_admin_delete_revokes_tokens_before_deleting_account(
        self, user_service, mock_user_repository, mock_token_repository, mocker, make_user
    ):
        user = make_user(is_admin=False)
        mock_user_repository.get_user_by_id.return_value = user
        events: list[str] = []
        mock_token_repository.delete_all_refresh_tokens_for_user.side_effect = lambda uid: (
            events.append("sessions")
        )
        mock_user_repository.delete_user.side_effect = lambda u: events.append("delete")
        mock_user_repository.commit.side_effect = lambda: events.append("commit")
        mocker.patch(
            "app.users.service.revoke_access_to_all_tokens",
            side_effect=lambda uid: events.append("revoke"),
        )

        await user_service.delete_user_by_id_service(user.id, admin_id=uuid4())

        assert events == ["revoke", "sessions", "delete", "commit"]

    async def test_admin_delete_redis_failure_deletes_nothing(
        self, user_service, mock_user_repository, mock_token_repository, mocker, make_user
    ):
        from redis.exceptions import ConnectionError as RedisConnectionError

        user = make_user(is_admin=False)
        mock_user_repository.get_user_by_id.return_value = user
        mocker.patch(
            "app.users.service.revoke_access_to_all_tokens",
            side_effect=RedisConnectionError("boom"),
        )

        with pytest.raises(RedisConnectionError):
            await user_service.delete_user_by_id_service(user.id, admin_id=uuid4())

        mock_token_repository.delete_all_refresh_tokens_for_user.assert_not_called()
        mock_user_repository.delete_user.assert_not_called()
        mock_user_repository.commit.assert_not_called()


class TestDeleteMultipleUsersByIdsService:
    async def test_self_delete_in_batch_blocked(self, user_service, mock_user_repository):
        admin_id = uuid4()

        with pytest.raises(ForbiddenError):
            await user_service.delete_multiple_users_by_ids_service(
                [admin_id, uuid4()], admin_id=admin_id
            )

        mock_user_repository.get_user_by_id.assert_not_called()

    async def test_mixed_batch_found_not_found_and_admin_skip(
        self, user_service, mock_user_repository, mock_redis, make_user
    ):
        not_found_id = uuid4()
        admin_user = make_user(is_admin=True)
        deletable_user = make_user(is_admin=False)
        mock_redis.zrangebyscore.return_value = []

        async def get_user_by_id(user_id):
            if user_id == admin_user.id:
                return admin_user
            if user_id == deletable_user.id:
                return deletable_user
            return None

        mock_user_repository.get_user_by_id.side_effect = get_user_by_id

        result = await user_service.delete_multiple_users_by_ids_service(
            [not_found_id, admin_user.id, deletable_user.id], admin_id=uuid4()
        )

        assert result.deleted == [deletable_user.id]
        assert result.not_found == [not_found_id]
        assert result.skipped_admins == [admin_user.id]
        mock_user_repository.delete_user.assert_called_once_with(deletable_user)

    async def test_each_user_has_tokens_revoked_before_being_deleted(
        self, user_service, mock_user_repository, mock_token_repository, mocker, make_user
    ):
        first, second = make_user(is_admin=False), make_user(is_admin=False)
        users = {first.id: first, second.id: second}

        async def get_user_by_id(user_id):
            return users[user_id]

        mock_user_repository.get_user_by_id.side_effect = get_user_by_id
        events: list[str] = []
        mock_token_repository.delete_all_refresh_tokens_for_user.side_effect = lambda uid: (
            events.append("sessions")
        )
        mock_user_repository.delete_user.side_effect = lambda u: events.append("delete")
        mock_user_repository.commit.side_effect = lambda: events.append("commit")
        mocker.patch(
            "app.users.service.revoke_access_to_all_tokens",
            side_effect=lambda uid: events.append("revoke"),
        )

        await user_service.delete_multiple_users_by_ids_service(
            [first.id, second.id], admin_id=uuid4()
        )

        assert events == ["revoke", "sessions", "delete", "commit"] * 2


class TestBulkDeleteRedisOutage:
    async def test_stops_at_first_redis_failure_and_reports_the_rest(
        self, user_service, mock_user_repository, mocker, make_user
    ):
        from redis.exceptions import ConnectionError as RedisConnectionError

        first, second, third = (make_user(is_admin=False) for _ in range(3))
        users = {u.id: u for u in (first, second, third)}

        async def get_user_by_id(user_id):
            return users[user_id]

        mock_user_repository.get_user_by_id.side_effect = get_user_by_id
        revoke = mocker.patch(
            "app.users.service.revoke_access_to_all_tokens",
            side_effect=[None, RedisConnectionError("boom")],
        )

        result = await user_service.delete_multiple_users_by_ids_service(
            [first.id, second.id, third.id], admin_id=uuid4()
        )

        assert result.deleted == [first.id]
        assert result.not_processed == [second.id, third.id]
        assert revoke.call_count == 2  # did not keep hammering a down Redis
        mock_user_repository.delete_user.assert_called_once_with(first)


class TestEnqueueFailureOnCommittedPaths:
    async def test_signup_returns_503_when_broker_is_down(
        self, user_service, mock_user_repository, mock_celery_tasks
    ):
        from app.exceptions import ServiceUnavailableError

        mock_user_repository.get_user_by_email.return_value = None
        mock_user_repository.create_user.side_effect = lambda u: u
        mock_celery_tasks["verification"].side_effect = OperationalError("broker down")

        with pytest.raises(ServiceUnavailableError):
            await user_service.create_user_service(
                UserCreateSchema(
                    email="new@example.com", username="newuser", password="Password123!"
                )
            )


class TestEmailRequestFailureHandling:
    async def test_cooldown_release_failure_still_gives_app_error(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        from redis.exceptions import ConnectionError as RedisConnectionError

        user = make_user(is_verified=False)
        mock_user_repository.get_user_by_email.return_value = user
        mock_redis.set.return_value = True
        mock_redis.get.return_value = None
        mock_celery_tasks["verification"].side_effect = OperationalError("broker down")
        mock_redis.delete.side_effect = RedisConnectionError("boom")

        with pytest.raises(ServiceUnavailableError, match="5 minutes"):
            await user_service.resend_email_verification_service(user.email)

    async def test_redis_failure_while_issuing_token_releases_cooldown(
        self, user_service, mock_user_repository, mock_redis, mock_celery_tasks, make_user
    ):
        from redis.exceptions import ConnectionError as RedisConnectionError

        user = make_user()
        mock_user_repository.get_user_by_email.return_value = user
        mock_redis.set.return_value = True
        mock_redis.get.return_value = None
        mock_redis.setex.side_effect = RedisConnectionError("boom")

        with pytest.raises(ServiceUnavailableError):
            await user_service.forgot_password_service(user.email)

        mock_redis.delete.assert_called_once_with(f"forgot_password_cooldown:{user.id}")
        mock_celery_tasks["password_reset"].assert_not_called()

    async def test_signup_redis_failure_says_account_was_created(
        self, user_service, mock_user_repository, mock_redis
    ):
        from redis.exceptions import ConnectionError as RedisConnectionError

        mock_user_repository.get_user_by_email.return_value = None
        mock_user_repository.create_user.side_effect = lambda u: u
        mock_redis.setex.side_effect = RedisConnectionError("boom")

        with pytest.raises(ServiceUnavailableError, match="account was created"):
            await user_service.create_user_service(
                UserCreateSchema(
                    email="new@example.com", username="newuser", password="Password123!"
                )
            )

        mock_user_repository.commit.assert_called_once()
