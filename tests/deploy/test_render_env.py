import os
import shutil
import subprocess
from pathlib import Path

import pytest

BASH = shutil.which("bash")
SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "render-env.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is not installed")

VALID = {
    "DB_URL": "postgresql+asyncpg://app:s3cretPass@db.example.rds.amazonaws.com:5432/postgres?ssl=require",
    "JWT_SECRET": "j" * 40,
    "RESEND_API_KEY": "re_abc123XYZ",
    "REDIS_PASSWORD": "redisPassword_1234-abcd",
    "API_HOSTNAME": "animeplanet-api.duckdns.org",
    "FRONTEND_URL": "https://animeplanet.vercel.app",
}


def run(**overrides):
    env = {k: v for k, v in os.environ.items() if k not in VALID}
    env.update(VALID)
    env.update({k: v for k, v in overrides.items() if v is not None})
    for name, value in overrides.items():
        if value is None:
            env.pop(name, None)
    return subprocess.run([BASH, str(SCRIPT)], env=env, capture_output=True, text=True)


def lines(result) -> dict:
    pairs = (line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return {key: value for key, value in pairs if not key.startswith("#")}


class TestOutput:
    def test_valid_values_render_the_env_file(self):
        result = run()

        assert result.returncode == 0, result.stderr
        out = lines(result)
        assert out["ENVIRONMENT"] == "production"
        assert out["HTTP_SECURE"] == "true"  # the refresh cookie is Secure: it needs HTTPS
        assert out["LOG_JSON"] == "true"
        assert out["DB_URL"] == VALID["DB_URL"]
        assert out["API_HOSTNAME"] == VALID["API_HOSTNAME"]

    def test_the_redis_urls_carry_the_password_and_the_three_databases(self):
        out = lines(run())

        password = VALID["REDIS_PASSWORD"]
        assert out["REDIS_URL"] == f"redis://:{password}@redis:6379/0"
        assert out["REDIS_BROKER_URL"] == f"redis://:{password}@redis:6379/1"
        assert out["REDIS_BACKEND_URL"] == f"redis://:{password}@redis:6379/2"

    def test_a_trailing_slash_on_the_frontend_url_is_dropped(self):
        out = lines(run(FRONTEND_URL="https://animeplanet.vercel.app/"))

        assert out["FRONTEND_URL"] == "https://animeplanet.vercel.app"

    def test_every_line_is_a_comment_a_blank_or_key_equals_value(self):
        for line in run().stdout.splitlines():
            assert line == "" or line.startswith("#") or "=" in line


class TestFrontendAndCors:
    @pytest.mark.parametrize(
        "url", ["http://localhost:3000", "http://localhost", "http://localhost:5173/"]
    )
    def test_a_local_frontend_is_accepted_while_it_is_being_built(self, url):
        result = run(FRONTEND_URL=url)

        assert result.returncode == 0, result.stderr
        assert lines(result)["FRONTEND_URL"] == url.rstrip("/")

    @pytest.mark.parametrize(
        "url", ["http://example.com", "http://localhost.evil.com", "http://127.0.0.1:3000"]
    )
    def test_any_other_plain_http_address_is_still_refused(self, url):
        result = run(FRONTEND_URL=url)

        assert result.returncode == 1
        assert "FRONTEND_URL" in result.stderr

    def test_there_are_no_extra_origins_by_default(self):
        assert lines(run())["CORS_EXTRA_ORIGINS"] == ""

    def test_extra_origins_are_passed_through(self):
        out = lines(run(CORS_EXTRA_ORIGINS="http://localhost:3000,http://localhost:5173"))

        assert out["CORS_EXTRA_ORIGINS"] == "http://localhost:3000,http://localhost:5173"

    def test_none_means_no_extra_origins(self):
        assert lines(run(CORS_EXTRA_ORIGINS="none"))["CORS_EXTRA_ORIGINS"] == ""

    @pytest.mark.parametrize(
        "value",
        [
            "*",
            "http://localhost:3000,*",
            "localhost:3000",
            "http://x.com/path",
            "javascript:alert(1)",
        ],
    )
    def test_a_wildcard_or_a_malformed_origin_is_refused(self, value):
        result = run(CORS_EXTRA_ORIGINS=value)

        assert result.returncode == 1
        assert "CORS_EXTRA_ORIGINS" in result.stderr
        assert result.stdout == ""


class TestCookie:
    def test_the_cookie_is_cross_site_in_production(self):
        # A frontend on another site (localhost, Vercel) can only keep a SameSite=None cookie.
        out = lines(run())

        assert out["COOKIE_SAMESITE"] == "none"
        assert out["HTTP_SECURE"] == "true"  # SameSite=None is only accepted together with Secure


class TestMissingValues:
    @pytest.mark.parametrize("name", list(VALID))
    def test_an_empty_value_fails_by_name_and_prints_nothing(self, name):
        result = run(**{name: ""})

        assert result.returncode == 1
        assert name in result.stderr
        assert result.stdout == ""  # CI must never send a half-written file

    @pytest.mark.parametrize("name", list(VALID))
    def test_an_unset_value_fails_by_name(self, name):
        result = run(**{name: None})

        assert result.returncode == 1
        assert name in result.stderr
        assert result.stdout == ""


class TestRefusedValues:
    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("DB_URL", "postgresql://app:pw@db:5432/postgres?ssl=require"),  # not asyncpg
            ("DB_URL", "postgresql+asyncpg://app:pw@db:5432/postgres"),  # no SSL
            ("JWT_SECRET", "short"),
            ("REDIS_PASSWORD", "tooshort"),
            ("REDIS_PASSWORD", "has/slash/in/it/1234567"),  # breaks the Redis URL
            ("API_HOSTNAME", "https://animeplanet-api.duckdns.org"),
            ("API_HOSTNAME", "animeplanet-api.duckdns.org/path"),
            ("API_HOSTNAME", "-bad.example.com"),
            ("FRONTEND_URL", "http://animeplanet.vercel.app"),
        ],
    )
    def test_a_value_that_looks_wrong_is_refused(self, name, value):
        result = run(**{name: value})

        assert result.returncode == 1
        assert name in result.stderr
        assert result.stdout == ""

    @pytest.mark.parametrize(
        "bad", ["has space", "has$dollar", 'has"quote', "has'quote", "back\\slash", "has#hash"]
    )
    def test_characters_an_env_file_cannot_hold_are_refused(self, bad):
        result = run(RESEND_API_KEY=bad)

        assert result.returncode == 1
        assert "RESEND_API_KEY" in result.stderr

    def test_a_value_with_a_line_break_is_refused(self):
        result = run(RESEND_API_KEY="re_abc\nINJECTED=1")

        assert result.returncode == 1
        assert result.stdout == ""

    @pytest.mark.parametrize("name", ["DB_URL", "JWT_SECRET", "REDIS_PASSWORD"])
    def test_an_error_never_contains_the_secret_value(self, name):
        bad = "bad value with spaces " + VALID[name]

        result = run(**{name: bad})

        assert result.returncode == 1
        assert VALID[name] not in result.stderr and VALID[name] not in result.stdout
