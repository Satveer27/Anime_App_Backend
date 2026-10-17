from app.core.database.database import engine


def test_engine_replaces_dead_pooled_connections_and_fails_fast():
    pool = engine.sync_engine.pool
    assert pool._pre_ping is True
    assert pool._recycle == 1800
    assert pool._timeout == 5
