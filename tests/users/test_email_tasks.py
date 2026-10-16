import pytest

from app.users.tasks import tasks
from app.users.utils import email as email_utils


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
        send = mocker.patch.object(
            tasks, sender, side_effect=[RuntimeError("boom"), RuntimeError("boom"), None]
        )

        result = task.apply(args=("user@example.com", "tok"))

        assert result.successful()
        assert send.call_count == 3

    def test_gives_up_after_max_retries(self, mocker, task, sender):
        send = mocker.patch.object(tasks, sender, side_effect=RuntimeError("down"))

        result = task.apply(args=("user@example.com", "tok"))

        assert result.failed()
        # first attempt + max_retries retries
        assert send.call_count == 1 + task.max_retries
        assert task.max_retries == 5
