import json

import pytest
import structlog

from app.core.logging import configure_logging


@pytest.fixture(autouse=True)
def restore_logging():
    yield
    configure_logging(False)


def last_line(capsys) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


class TestJsonLogging:
    def test_a_log_call_is_one_json_line_with_its_fields(self, capsys):
        configure_logging(True)

        structlog.get_logger().info("refresh_token_revoked", user_id=7, reason="logout")

        record = json.loads(last_line(capsys))
        assert record["event"] == "refresh_token_revoked"
        assert record["user_id"] == 7 and record["reason"] == "logout"
        assert record["level"] == "info"
        assert record["timestamp"].endswith("Z")

    def test_an_exception_is_one_line_too(self, capsys):
        configure_logging(True)

        try:
            raise ValueError("boom")
        except ValueError:
            structlog.get_logger().error("job_failed", exc_info=True)

        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1
        assert "ValueError: boom" in json.loads(out[0])["exception"]

    def test_debug_is_not_printed(self, capsys):
        configure_logging(True)

        structlog.get_logger().debug("noisy")

        assert capsys.readouterr().out == ""


class TestReadableLogging:
    def test_development_output_is_not_json(self, capsys):
        configure_logging(False)

        structlog.get_logger().info("app_ready", environment="local")

        line = last_line(capsys)
        assert "app_ready" in line
        with pytest.raises(json.JSONDecodeError):
            json.loads(line)
