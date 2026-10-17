from unittest.mock import AsyncMock, MagicMock

import pytest

from app.security.tasks import tasks


@pytest.fixture
def session(mocker):
    db = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    mocker.patch.object(tasks, "asyncSessionMaker", return_value=ctx)
    mocker.patch.object(tasks, "engine", dispose=AsyncMock())
    return db


async def test_engine_is_disposed_after_success(mocker, session):
    repo = mocker.patch.object(tasks, "create_refresh_token_repository").return_value
    repo.cleanup_expired_refresh_tokens = AsyncMock(return_value=3)

    await tasks.refresh_token_scheduler()

    tasks.engine.dispose.assert_awaited_once()


async def test_engine_is_disposed_when_cleanup_fails(mocker, session):
    repo = mocker.patch.object(tasks, "create_refresh_token_repository").return_value
    repo.cleanup_expired_refresh_tokens = AsyncMock(side_effect=RuntimeError("db down"))

    with pytest.raises(RuntimeError):
        await tasks.refresh_token_scheduler()

    tasks.engine.dispose.assert_awaited_once()


def test_cleanup_task_retries_twice():
    assert tasks.cleanup_expired_refresh_tokens.max_retries == 2
    assert Exception in tasks.cleanup_expired_refresh_tokens.autoretry_for
