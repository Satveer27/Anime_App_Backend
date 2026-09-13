import pytest

from app.main import app
from app.security.deps import create_jwt_service
from app.security.service import JWTService


@pytest.fixture
def fake_jwt_service(mocker, client):
    """Autospec'd AsyncMock standing in for the whole JWTService, wired in
    as the `create_jwt_service` dependency."""
    fake = mocker.create_autospec(JWTService, instance=True)
    app.dependency_overrides[create_jwt_service] = lambda: fake
    return fake
