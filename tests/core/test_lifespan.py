from unittest.mock import AsyncMock

import pytest

from app import main


@pytest.fixture
def deps(mocker):
    check = mocker.patch.object(main, "_check_dependencies", new=AsyncMock())
    sleep = mocker.patch.object(main.asyncio, "sleep", new=AsyncMock())
    redis_close = mocker.patch.object(main.redis_server, "aclose", new=AsyncMock())
    engine = mocker.patch.object(main, "engine")
    engine.dispose = AsyncMock()
    return {"check": check, "sleep": sleep, "redis_close": redis_close, "engine": engine}


async def test_startup_succeeds_first_try_without_sleeping(deps):
    async with main.lifespan(main.app):
        pass

    deps["check"].assert_awaited_once()
    deps["sleep"].assert_not_awaited()


async def test_startup_retries_twice_then_succeeds(deps):
    deps["check"].side_effect = [OSError("db starting"), OSError("db starting"), None]

    async with main.lifespan(main.app):
        pass

    assert deps["check"].await_count == 3
    assert [c.args[0] for c in deps["sleep"].await_args_list] == [2, 4]


async def test_startup_gives_up_after_three_attempts_and_still_cleans_up(deps):
    deps["check"].side_effect = OSError("db down")

    with pytest.raises(OSError):
        async with main.lifespan(main.app):
            pass

    assert deps["check"].await_count == 3
    deps["redis_close"].assert_awaited_once()
    deps["engine"].dispose.assert_awaited_once()


async def test_shutdown_disposes_engine_even_if_redis_close_fails(deps):
    deps["redis_close"].side_effect = RuntimeError("boom")

    async with main.lifespan(main.app):
        pass

    deps["engine"].dispose.assert_awaited_once()


async def test_shutdown_cleanup_runs_when_the_app_body_raises(deps):
    with pytest.raises(RuntimeError):
        async with main.lifespan(main.app):
            raise RuntimeError("crash while serving")

    deps["redis_close"].assert_awaited_once()
    deps["engine"].dispose.assert_awaited_once()
