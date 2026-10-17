from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

from app.main import app
from app.security.utils.redis_util import is_token_revoked

router = APIRouter()


@router.get("/_test/redis-down")
async def _redis_down():
    raise RedisConnectionError("boom")


@router.get("/_test/db-down")
async def _db_down():
    raise OperationalError("SELECT 1", {}, Exception("connection lost"))


@router.get("/_test/bug")
async def _bug():
    raise ValueError("a real bug")


app.include_router(router)


@pytest.mark.parametrize("path", ["/_test/redis-down", "/_test/db-down"])
def test_dependency_errors_map_to_503_with_retry_after(client, path):
    response = client.get(path)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json()["status_code"] == 503


def test_unrelated_exceptions_stay_500(client):
    # raise_server_exceptions=False so the generic handler's response is returned
    from fastapi.testclient import TestClient

    response = TestClient(app, raise_server_exceptions=False).get("/_test/bug")

    assert response.status_code == 500


async def test_redis_error_propagates_from_revocation_check(mocker):
    mocker.patch(
        "app.security.utils.redis_util.redis_server",
        AsyncMock(exists=AsyncMock(side_effect=RedisConnectionError("boom"))),
    )

    with pytest.raises(RedisConnectionError):
        await is_token_revoked("some-jti")
