import pytest
import resend

from app.users.tasks import tasks
from app.users.utils import email as email_utils


def resend_error(code, error_type):
    return resend.exceptions.ResendError(
        code=code, error_type=error_type, message="x", suggested_action=""
    )


# what the SDK raises when the network call itself fails
TRANSIENT = RuntimeError("Request failed: timed out")


class TestIsTransientEmailError:
    @pytest.mark.parametrize(
        "exc",
        [
            TRANSIENT,
            resend_error(500, "application_error"),
            resend_error("503", "unknown"),
            resend_error(429, "rate_limit_exceeded"),
            resend_error(409, "concurrent_idempotent_requests"),
        ],
    )
    def test_retryable(self, exc):
        assert email_utils.is_transient_email_error(exc) is True

    @pytest.mark.parametrize(
        "exc",
        [
            resend_error(422, "validation_error"),
            resend_error(400, "validation_error"),
            resend_error(403, "invalid_api_key"),
            resend_error(429, "daily_quota_exceeded"),
            resend_error(429, "monthly_quota_exceeded"),
            resend_error(409, "invalid_idempotent_request"),
            ValueError("bad"),
            RuntimeError("something else"),
        ],
    )
    def test_not_retryable(self, exc):
        assert email_utils.is_transient_email_error(exc) is False


class TestResendIdempotencyKey:
    def test_verification_email_sends_key_derived_from_token(self, mocker):
        send = mocker.patch.object(email_utils.resend.Emails, "send", return_value={"id": "x"})

        email_utils.send_email_verification("user@example.com", "tok123")

        assert send.call_args.args[1] == {"idempotency_key": "verify-email/tok123"}

    def test_password_reset_email_sends_key_derived_from_token(self, mocker):
        send = mocker.patch.object(email_utils.resend.Emails, "send", return_value={"id": "x"})

        email_utils.send_email_password_reset("user@example.com", "tok456")

        assert send.call_args.args[1] == {"idempotency_key": "password-reset/tok456"}

    def test_same_token_always_gives_same_key_so_retries_dedupe(self, mocker):
        send = mocker.patch.object(email_utils.resend.Emails, "send", return_value={"id": "x"})

        email_utils.send_email_verification("user@example.com", "tok123")
        email_utils.send_email_verification("user@example.com", "tok123")

        first, second = send.call_args_list
        assert first.args[1] == second.args[1]


@pytest.mark.parametrize(
    ("task", "sender"),
    [
        (tasks.send_email_verification_task, "send_email_verification"),
        (tasks.send_email_password_reset_task, "send_email_password_reset"),
    ],
)
class TestEmailTaskRetries:
    def test_retries_transient_failure_then_succeeds(self, mocker, task, sender):
        send = mocker.patch.object(tasks, sender, side_effect=[TRANSIENT, TRANSIENT, None])

        result = task.apply(args=("user@example.com", "tok"))

        assert result.successful()
        assert send.call_count == 3

    def test_gives_up_after_max_retries(self, mocker, task, sender):
        send = mocker.patch.object(tasks, sender, side_effect=TRANSIENT)

        result = task.apply(args=("user@example.com", "tok"))

        assert result.failed()
        # first attempt + max_retries retries
        assert send.call_count == 1 + task.max_retries
        assert task.max_retries == 5

    def test_permanent_failure_is_not_retried(self, mocker, task, sender):
        send = mocker.patch.object(tasks, sender, side_effect=resend_error(422, "validation_error"))

        result = task.apply(args=("not-an-email", "tok"))

        assert result.failed()
        assert send.call_count == 1
