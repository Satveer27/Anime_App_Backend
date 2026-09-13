from uuid import uuid4

from app.exceptions import AuthenticationError, ForbiddenError
from app.schemas import SuccessMessage
from app.users.schemas import BulkDeleteResult, PaginatedUsersResponse, UserResponseSchema

BASE = "/api/v1/users"
ADMIN = f"{BASE}/admin/users"


class TestSignupEndpoint:
    def test_success(self, client, fake_user_service, make_user):
        created_user = make_user(email="new@example.com", username="newuser")
        fake_user_service.create_user_service.return_value = UserResponseSchema.model_validate(created_user)

        response = client.post(
            f"{BASE}/signup",
            json={"email": "new@example.com", "password": "Password123!", "username": "newuser"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "new@example.com"
        assert body["username"] == "newuser"
        # the fact this doesn't raise proves the response actually satisfies
        # UserResponseSchema, not just "some JSON that happens to look right"
        UserResponseSchema.model_validate(body)

        call_payload = fake_user_service.create_user_service.call_args.args[0]
        assert call_payload.email == "new@example.com"

    def test_malformed_body_returns_422_not_500(self, client, fake_user_service):
        response = client.post(
            f"{BASE}/signup",
            json={"email": "not-an-email", "password": "short", "username": "n"},
        )

        assert response.status_code == 422
        fake_user_service.create_user_service.assert_not_called()


class TestResendVerificationEndpoint:
    def test_success(self, client, fake_user_service):
        fake_user_service.resend_email_verification_service.return_value = SuccessMessage(success_message="ok")

        response = client.post(f"{BASE}/resend-verification", json={"email": "a@example.com"})

        assert response.status_code == 200
        SuccessMessage.model_validate(response.json())
        fake_user_service.resend_email_verification_service.assert_called_once_with("a@example.com")

    def test_malformed_body_returns_422_not_500(self, client, fake_user_service):
        response = client.post(f"{BASE}/resend-verification", json={"email": "not-an-email"})

        assert response.status_code == 422
        fake_user_service.resend_email_verification_service.assert_not_called()


class TestForgotPasswordEndpoint:
    def test_success(self, client, fake_user_service):
        fake_user_service.forgot_password_service.return_value = SuccessMessage(success_message="ok")

        response = client.post(f"{BASE}/forgot-password", json={"email": "a@example.com"})

        assert response.status_code == 200
        SuccessMessage.model_validate(response.json())
        fake_user_service.forgot_password_service.assert_called_once_with("a@example.com")

    def test_malformed_body_returns_422_not_500(self, client, fake_user_service):
        response = client.post(f"{BASE}/forgot-password", json={"email": "not-an-email"})

        assert response.status_code == 422
        fake_user_service.forgot_password_service.assert_not_called()


class TestResetPasswordEndpoint:
    def test_success(self, client, fake_user_service):
        fake_user_service.reset_password_service_via_email.return_value = SuccessMessage(success_message="ok")

        response = client.post(
            f"{BASE}/reset-password",
            params={"token": "sometoken"},
            json={"password": "NewPass123!", "confirm_password": "NewPass123!"},
        )

        assert response.status_code == 200
        SuccessMessage.model_validate(response.json())
        fake_user_service.reset_password_service_via_email.assert_called_once_with("sometoken", "NewPass123!")

    def test_missing_token_query_param_returns_422_not_500(self, client, fake_user_service):
        response = client.post(
            f"{BASE}/reset-password",
            json={"password": "NewPass123!", "confirm_password": "NewPass123!"},
        )

        assert response.status_code == 422
        fake_user_service.reset_password_service_via_email.assert_not_called()

    def test_password_mismatch_returns_422_not_500(self, client, fake_user_service):
        response = client.post(
            f"{BASE}/reset-password",
            params={"token": "sometoken"},
            json={"password": "NewPass123!", "confirm_password": "Different123!"},
        )

        assert response.status_code == 422
        fake_user_service.reset_password_service_via_email.assert_not_called()


class TestVerifyEmailEndpoint:
    def test_success(self, client, fake_user_service):
        fake_user_service.verify_user_email_service.return_value = SuccessMessage(success_message="ok")

        response = client.get(f"{BASE}/verify-email", params={"token": "sometoken"})

        assert response.status_code == 200
        SuccessMessage.model_validate(response.json())
        fake_user_service.verify_user_email_service.assert_called_once_with("sometoken")

    def test_missing_token_query_param_returns_422_not_500(self, client, fake_user_service):
        response = client.get(f"{BASE}/verify-email")

        assert response.status_code == 422
        fake_user_service.verify_user_email_service.assert_not_called()


class TestMeEndpoint:
    def test_success(self, client, fake_user_service, as_current_user, make_user):
        user = make_user()
        as_current_user(user)
        fake_user_service.get_user_by_id_service.return_value = UserResponseSchema.model_validate(user)

        response = client.get(f"{BASE}/me")

        assert response.status_code == 200
        UserResponseSchema.model_validate(response.json())
        fake_user_service.get_user_by_id_service.assert_called_once_with(user.id)

    def test_rejected_when_dependency_raises(self, client, fake_user_service, as_current_user):
        as_current_user(AuthenticationError())

        response = client.get(f"{BASE}/me")

        assert response.status_code == 401
        fake_user_service.get_user_by_id_service.assert_not_called()


class TestUpdatePasswordEndpoint:
    def test_success(self, client, fake_user_service, as_current_user, make_user):
        user = make_user()
        as_current_user(user)
        fake_user_service.update_user_password_service.return_value = SuccessMessage(success_message="ok")

        response = client.put(
            f"{BASE}/update-password",
            json={"old_password": "OldPass123!", "new_password": "NewPass123!", "confirm_new_password": "NewPass123!"},
        )

        assert response.status_code == 200
        SuccessMessage.model_validate(response.json())
        fake_user_service.update_user_password_service.assert_called_once_with(user.id, "NewPass123!", "OldPass123!")

    def test_malformed_body_returns_422_not_500(self, client, fake_user_service, as_current_user, make_user):
        as_current_user(make_user())

        response = client.put(
            f"{BASE}/update-password",
            json={"old_password": "OldPass123!", "new_password": "NewPass123!", "confirm_new_password": "Different123!"},
        )

        assert response.status_code == 422
        fake_user_service.update_user_password_service.assert_not_called()

    def test_rejected_when_dependency_raises(self, client, fake_user_service, as_current_user):
        as_current_user(AuthenticationError())

        response = client.put(
            f"{BASE}/update-password",
            json={"old_password": "OldPass123!", "new_password": "NewPass123!", "confirm_new_password": "NewPass123!"},
        )

        assert response.status_code == 401
        fake_user_service.update_user_password_service.assert_not_called()


class TestUpdateEmailEndpoint:
    def test_success(self, client, fake_user_service, as_current_user, make_user):
        user = make_user()
        as_current_user(user)
        fake_user_service.update_email_service.return_value = SuccessMessage(success_message="ok")

        response = client.put(f"{BASE}/update-email", json={"email": "new@example.com"})

        assert response.status_code == 200
        SuccessMessage.model_validate(response.json())
        fake_user_service.update_email_service.assert_called_once_with(user.id, "new@example.com")

    def test_malformed_body_returns_422_not_500(self, client, fake_user_service, as_current_user, make_user):
        as_current_user(make_user())

        response = client.put(f"{BASE}/update-email", json={"email": "not-an-email"})

        assert response.status_code == 422
        fake_user_service.update_email_service.assert_not_called()

    def test_rejected_when_dependency_raises(self, client, fake_user_service, as_current_user):
        as_current_user(AuthenticationError())

        response = client.put(f"{BASE}/update-email", json={"email": "new@example.com"})

        assert response.status_code == 401
        fake_user_service.update_email_service.assert_not_called()


class TestUpdateUserEndpoint:
    def test_success(self, client, fake_user_service, as_current_user, make_user):
        user = make_user(username="oldname")
        as_current_user(user)
        updated = make_user(username="newname")
        fake_user_service.update_user_fields_service.return_value = UserResponseSchema.model_validate(updated)

        response = client.patch(f"{BASE}/update-user", json={"username": "newname"})

        assert response.status_code == 200
        UserResponseSchema.model_validate(response.json())
        assert response.json()["username"] == "newname"

    def test_malformed_body_returns_422_not_500(self, client, fake_user_service, as_current_user, make_user):
        as_current_user(make_user())

        response = client.patch(f"{BASE}/update-user", json={"username": "n"})

        assert response.status_code == 422
        fake_user_service.update_user_fields_service.assert_not_called()

    def test_rejected_when_dependency_raises(self, client, fake_user_service, as_current_user):
        as_current_user(AuthenticationError())

        response = client.patch(f"{BASE}/update-user", json={"username": "newname"})

        assert response.status_code == 401
        fake_user_service.update_user_fields_service.assert_not_called()


class TestDeleteUserEndpoint:
    def test_success(self, client, fake_user_service, as_current_user, make_user):
        user = make_user()
        as_current_user(user)
        fake_user_service.delete_current_user_service.return_value = SuccessMessage(success_message="ok")

        response = client.delete(f"{BASE}/delete-user")

        assert response.status_code == 200
        SuccessMessage.model_validate(response.json())
        fake_user_service.delete_current_user_service.assert_called_once_with(user.id)

    def test_rejected_when_dependency_raises(self, client, fake_user_service, as_current_user):
        as_current_user(AuthenticationError())

        response = client.delete(f"{BASE}/delete-user")

        assert response.status_code == 401
        fake_user_service.delete_current_user_service.assert_not_called()


class TestAdminGetUserByIdEndpoint:
    def test_success(self, client, fake_user_service, as_admin_user, make_user):
        admin = make_user(is_admin=True)
        target = make_user()
        as_admin_user(admin)
        fake_user_service.get_user_by_id_service.return_value = UserResponseSchema.model_validate(target)

        response = client.get(f"{ADMIN}/{target.id}")

        assert response.status_code == 200
        UserResponseSchema.model_validate(response.json())
        fake_user_service.get_user_by_id_service.assert_called_once_with(target.id)

    def test_malformed_uuid_path_param_returns_422_not_500(self, client, fake_user_service, as_admin_user, make_user):
        as_admin_user(make_user(is_admin=True))

        response = client.get(f"{ADMIN}/not-a-uuid")

        assert response.status_code == 422
        fake_user_service.get_user_by_id_service.assert_not_called()

    def test_rejected_when_dependency_raises(self, client, fake_user_service, as_admin_user):
        as_admin_user(ForbiddenError())

        response = client.get(f"{ADMIN}/{uuid4()}")

        assert response.status_code == 403
        fake_user_service.get_user_by_id_service.assert_not_called()


class TestAdminGetAllUsersEndpoint:
    def test_success(self, client, fake_user_service, as_admin_user, make_user):
        as_admin_user(make_user(is_admin=True))
        fake_user_service.get_all_users_service.return_value = PaginatedUsersResponse(
            items=[UserResponseSchema.model_validate(make_user())],
            total=1,
            page_size=20,
            page=1,
        )

        response = client.get(ADMIN, params={"page": 1, "page_size": 20})

        assert response.status_code == 200
        PaginatedUsersResponse.model_validate(response.json())
        assert response.json()["total"] == 1

    def test_malformed_query_param_returns_422_not_500(self, client, fake_user_service, as_admin_user, make_user):
        as_admin_user(make_user(is_admin=True))

        response = client.get(ADMIN, params={"page": 1, "page_size": 1000})  # le=100

        assert response.status_code == 422
        fake_user_service.get_all_users_service.assert_not_called()

    def test_rejected_when_dependency_raises(self, client, fake_user_service, as_admin_user):
        as_admin_user(ForbiddenError())

        response = client.get(ADMIN)

        assert response.status_code == 403
        fake_user_service.get_all_users_service.assert_not_called()


class TestAdminUpdateUserEndpoint:
    def test_success(self, client, fake_user_service, as_admin_user, make_user):
        admin = make_user(is_admin=True)
        target = make_user(email="old@example.com")
        as_admin_user(admin)
        updated = make_user(email="new@example.com")
        fake_user_service.update_user_email_by_id_service.return_value = UserResponseSchema.model_validate(updated)

        response = client.put(f"{ADMIN}/update/{target.id}", json={"email": "new@example.com"})

        assert response.status_code == 200
        UserResponseSchema.model_validate(response.json())
        fake_user_service.update_user_email_by_id_service.assert_called_once_with(target.id, "new@example.com", admin.id)

    def test_malformed_uuid_path_param_returns_422_not_500(self, client, fake_user_service, as_admin_user, make_user):
        as_admin_user(make_user(is_admin=True))

        response = client.put(f"{ADMIN}/update/not-a-uuid", json={"email": "new@example.com"})

        assert response.status_code == 422
        fake_user_service.update_user_email_by_id_service.assert_not_called()

    def test_malformed_body_returns_422_not_500(self, client, fake_user_service, as_admin_user, make_user):
        as_admin_user(make_user(is_admin=True))

        response = client.put(f"{ADMIN}/update/{uuid4()}", json={"email": "not-an-email"})

        assert response.status_code == 422
        fake_user_service.update_user_email_by_id_service.assert_not_called()

    def test_rejected_when_dependency_raises(self, client, fake_user_service, as_admin_user):
        as_admin_user(ForbiddenError())

        response = client.put(f"{ADMIN}/update/{uuid4()}", json={"email": "new@example.com"})

        assert response.status_code == 403
        fake_user_service.update_user_email_by_id_service.assert_not_called()


class TestAdminDeleteUserEndpoint:
    def test_success(self, client, fake_user_service, as_admin_user, make_user):
        admin = make_user(is_admin=True)
        target = make_user()
        as_admin_user(admin)
        fake_user_service.delete_user_by_id_service.return_value = SuccessMessage(success_message="ok")

        response = client.delete(f"{ADMIN}/delete/{target.id}")

        assert response.status_code == 200
        SuccessMessage.model_validate(response.json())
        fake_user_service.delete_user_by_id_service.assert_called_once_with(target.id, admin_id=admin.id)

    def test_malformed_uuid_path_param_returns_422_not_500(self, client, fake_user_service, as_admin_user, make_user):
        as_admin_user(make_user(is_admin=True))

        response = client.delete(f"{ADMIN}/delete/not-a-uuid")

        assert response.status_code == 422
        fake_user_service.delete_user_by_id_service.assert_not_called()

    def test_rejected_when_dependency_raises(self, client, fake_user_service, as_admin_user):
        as_admin_user(ForbiddenError())

        response = client.delete(f"{ADMIN}/delete/{uuid4()}")

        assert response.status_code == 403
        fake_user_service.delete_user_by_id_service.assert_not_called()


class TestAdminBulkDeleteEndpoint:
    def test_success(self, client, fake_user_service, as_admin_user, make_user):
        as_admin_user(make_user(is_admin=True))
        ids = [uuid4(), uuid4()]
        fake_user_service.delete_multiple_users_by_ids_service.return_value = BulkDeleteResult(
            deleted=ids, not_found=[], skipped_admins=[]
        )

        response = client.post(f"{ADMIN}/bulk-delete", json={"user_ids": [str(i) for i in ids]})

        assert response.status_code == 200
        BulkDeleteResult.model_validate(response.json())
        assert len(response.json()["deleted"]) == 2

    def test_malformed_body_returns_422_not_500(self, client, fake_user_service, as_admin_user, make_user):
        as_admin_user(make_user(is_admin=True))

        response = client.post(f"{ADMIN}/bulk-delete", json={"user_ids": []})  # min_length=1

        assert response.status_code == 422
        fake_user_service.delete_multiple_users_by_ids_service.assert_not_called()

    def test_rejected_when_dependency_raises(self, client, fake_user_service, as_admin_user):
        as_admin_user(ForbiddenError())

        response = client.post(f"{ADMIN}/bulk-delete", json={"user_ids": [str(uuid4())]})

        assert response.status_code == 403
        fake_user_service.delete_multiple_users_by_ids_service.assert_not_called()
