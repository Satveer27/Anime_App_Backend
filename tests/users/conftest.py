import pytest

from app.core.deps import get_current_user, require_admin
from app.main import app
from app.users.deps import create_user_service
from app.users.service import UserService


@pytest.fixture
def fake_user_service(mocker, client):
    fake = mocker.create_autospec(UserService, instance=True)
    app.dependency_overrides[create_user_service] = lambda: fake
    return fake


@pytest.fixture
def as_current_user(client):

    def _apply(user_or_exc):
        if isinstance(user_or_exc, Exception):
            async def _dependency():
                raise user_or_exc
        else:
            async def _dependency():
                return user_or_exc

        app.dependency_overrides[get_current_user] = _dependency

    return _apply


@pytest.fixture
def as_admin_user(client):
    """Same as as_current_user, but for the require_admin dependency."""

    def _apply(user_or_exc):
        if isinstance(user_or_exc, Exception):
            async def _dependency():
                raise user_or_exc
        else:
            async def _dependency():
                return user_or_exc

        app.dependency_overrides[require_admin] = _dependency

    return _apply
