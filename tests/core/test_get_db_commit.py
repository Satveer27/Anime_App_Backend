import inspect
from unittest.mock import AsyncMock, MagicMock

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.deps import create_refresh_token_repository, create_user_repository


def test_repository_factories_use_function_scope():
    for factory in (create_user_repository, create_refresh_token_repository):
        assert inspect.signature(factory).parameters["db"].default.scope == "function"


def test_failed_commit_is_an_error_response_not_a_silent_2xx(mocker):
    session = AsyncMock()
    session.commit.side_effect = OperationalError("COMMIT", {}, Exception("connection lost"))
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)
    mocker.patch("app.core.database.database.asyncSessionMaker", return_value=session_ctx)

    app = FastAPI()

    @app.get("/")
    async def route(_repo=Depends(create_user_repository)):
        return {"ok": True}

    response = TestClient(app, raise_server_exceptions=False).get("/")

    assert response.status_code == 500
    session.rollback.assert_awaited()
