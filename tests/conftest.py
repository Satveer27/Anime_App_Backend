import os


os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DB_URL", "postgresql+asyncpg://test:test@localhost/test_db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REDIS_BROKER_URL", "redis://localhost:6379/1")
os.environ.setdefault("REDIS_BACKEND_URL", "redis://localhost:6379/2")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-bytes-long")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("REFRESH_TOKEN_EXPIRE_DAYS", "7")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
os.environ.setdefault("RESEND_API_KEY", "test-resend-key")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.security.models import RefreshToken
from app.security.repository import RefreshTokenRepository
from app.security.service import JWTService
from app.security.utils.password import hash_password
from app.users.models import User
from app.users.repository import UserRepository
from app.users.service import UserService


@pytest.fixture(autouse=True)
def mock_celery_tasks(mocker):
    return {
        "verification": mocker.patch("app.users.tasks.tasks.send_email_verification_task.delay"),
        "password_reset": mocker.patch("app.users.tasks.tasks.send_email_password_reset_task.delay"),
    }


@pytest.fixture
def mock_redis(mocker):
    redis_mock = AsyncMock()
    mocker.patch("app.users.service.redis_server", redis_mock)
    mocker.patch("app.security.utils.redis_util.redis_server", redis_mock)
    return redis_mock


@pytest.fixture
def mock_user_repository(mocker):
    return mocker.create_autospec(UserRepository, instance=True)


@pytest.fixture
def mock_token_repository(mocker):
    return mocker.create_autospec(RefreshTokenRepository, instance=True)


@pytest.fixture
def user_service(mock_user_repository, mock_token_repository, mock_redis):
    return UserService(mock_user_repository, mock_token_repository)


@pytest.fixture
def jwt_service(mock_token_repository, mock_user_repository, mock_redis):
    # Constructor order is (refresh_token_repository, user_repository).
    return JWTService(mock_token_repository, mock_user_repository)


@pytest.fixture
def make_user():

    def _make(
        *,
        user_id: uuid.UUID | None = None,
        email: str = "user@example.com",
        username: str = "testuser",
        password: str = "Password123!",
        is_admin: bool = False,
        is_verified: bool = True,
    ) -> User:
        return User(
            id=user_id or uuid.uuid4(),
            email=email,
            username=username,
            password=hash_password(password),
            is_admin=is_admin,
            is_verified=is_verified,
            created_at=datetime.now(timezone.utc),
        )

    return _make


@pytest.fixture
def make_refresh_token():

    def _make(
        *,
        user_id: uuid.UUID,
        jti: uuid.UUID | None = None,
        revoke: bool = False,
        expires_at: datetime | None = None,
    ) -> RefreshToken:
        return RefreshToken(
            jti=jti or uuid.uuid4(),
            user_id=user_id,
            revoke=revoke,
            expires_at=expires_at or datetime.now(timezone.utc),
        )

    return _make


@pytest.fixture
def client():
    """Plain TestClient, not used as a context manager on purpose: entering
    it as `with TestClient(app) as c:` would run the app's lifespan (real DB
    connect + Redis ping), which this pass has no real services for. Every
    controller test overrides the top-level service-factory/auth dependency
    for the route it hits, so nothing in these tests ever reaches get_db or
    the real redis_server anyway.
    """
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()
