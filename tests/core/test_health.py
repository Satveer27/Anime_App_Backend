import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core import health


@pytest.fixture
def healthy_deps(mocker):
    conn = AsyncMock()
    connect_ctx = MagicMock()
    connect_ctx.__aenter__ = AsyncMock(return_value=conn)
    connect_ctx.__aexit__ = AsyncMock(return_value=False)
    engine = mocker.patch.object(health, "engine")
    engine.connect.return_value = connect_ctx
    ping = mocker.patch.object(health.redis_server, "ping", new=AsyncMock(return_value=True))
    return {"connect": engine.connect, "ping": ping}


def test_liveness_never_touches_dependencies(client, mocker):
    connect = mocker.patch.object(health, "engine").connect
    ping = mocker.patch.object(health.redis_server, "ping", new=AsyncMock())

    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    connect.assert_not_called()
    ping.assert_not_called()


def test_ready_when_all_dependencies_up(client, healthy_deps):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "checks": {"database": "ok", "redis": "ok"},
    }


def test_unhealthy_503_when_database_down(client, healthy_deps):
    healthy_deps["connect"].side_effect = OSError("connection refused")

    response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unhealthy",
        "checks": {"database": "down", "redis": "ok"},
    }


def test_unhealthy_503_when_redis_down(client, healthy_deps):
    healthy_deps["ping"].side_effect = RedisConnectionError("boom")

    response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "down"}


def test_error_details_are_not_leaked(client, healthy_deps):
    healthy_deps["ping"].side_effect = RedisConnectionError("redis://user:secret@host")

    response = client.get("/api/v1/health")

    assert "secret" not in response.text


def test_hung_dependency_times_out(client, healthy_deps, mocker):
    async def hang():
        await asyncio.sleep(60)

    healthy_deps["ping"].side_effect = hang
    mocker.patch.object(health, "CHECK_TIMEOUT_S", 0.05)

    response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "down"
