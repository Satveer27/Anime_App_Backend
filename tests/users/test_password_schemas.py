import pytest
from pydantic import ValidationError

from app.security.schemas import UserRequestLogin
from app.users.schemas import ResetPasswordSchema, UpdatePasswordSchema, UserCreateSchema

TOO_LONG_BYTES = "é" * 40  # 40 characters but 80 bytes
OK_MULTIBYTE = "é" * 36  # 72 bytes exactly


def test_signup_rejects_password_over_72_bytes():
    with pytest.raises(ValidationError, match="72 bytes"):
        UserCreateSchema(email="a@example.com", username="abc", password=TOO_LONG_BYTES)


def test_signup_accepts_password_of_exactly_72_bytes():
    UserCreateSchema(email="a@example.com", username="abc", password=OK_MULTIBYTE)


def test_reset_password_rejects_over_72_bytes():
    with pytest.raises(ValidationError, match="72 bytes"):
        ResetPasswordSchema(password=TOO_LONG_BYTES, confirm_password=TOO_LONG_BYTES)


def test_update_password_rejects_over_72_bytes():
    with pytest.raises(ValidationError, match="72 bytes"):
        UpdatePasswordSchema(
            old_password="Password123!",
            new_password=TOO_LONG_BYTES,
            confirm_new_password=TOO_LONG_BYTES,
        )


def test_login_rejects_over_72_bytes():
    with pytest.raises(ValidationError, match="72 bytes"):
        UserRequestLogin(email="a@example.com", password=TOO_LONG_BYTES)


def test_signup_endpoint_returns_422_not_500(client):
    response = client.post(
        "/api/v1/users/signup",
        json={"email": "a@example.com", "username": "abc", "password": TOO_LONG_BYTES},
    )

    assert response.status_code == 422
