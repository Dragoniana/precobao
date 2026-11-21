import logging

from app.services import password_reset_email as mailer


def test_smtp_message_is_multipart_and_requires_verified_starttls(monkeypatch):
    captured = {}
    tls_context = object()

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            captured["connection"] = (host, port, timeout)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def ehlo(self):
            captured["ehlo"] = captured.get("ehlo", 0) + 1

        def starttls(self, context):
            captured["tls_context"] = context

        def login(self, username, password):
            captured["login"] = (username, password)

        def send_message(self, message):
            captured["message"] = message
            return {}

    for name, value in {
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "587",
        "SMTP_STARTTLS": "true",
        "SMTP_USERNAME": "mailer@example.com",
        "SMTP_PASSWORD": "test-only-app-password",
        "SMTP_FROM_EMAIL": "mailer@example.com",
        "SMTP_FROM_NAME": "Preço Bão",
        "SMTP_TIMEOUT_SECONDS": "10",
        "PASSWORD_RESET_URL": "https://precobao.app/reset-password",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer.ssl, "create_default_context", lambda: tls_context)

    mailer._send_password_reset_email(
        "recipient@example.com", "test-reset-token-012345678901234567890"
    )

    assert captured["connection"] == ("smtp.gmail.com", 587, 10)
    assert captured["tls_context"] is tls_context
    assert captured["ehlo"] == 2
    message = captured["message"]
    assert message["Subject"] == "Recuperação de senha — Preço Bão"
    assert message["To"] == "recipient@example.com"
    assert message.is_multipart()
    assert (
        "expira em 20 minutos"
        in message.get_body(preferencelist=("plain",)).get_content()
    )
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "Redefinir minha senha" in html
    assert "https://precobao.app/reset-password?token=test-reset-token" in html


def test_smtp_refuses_non_tls_configuration(monkeypatch):
    monkeypatch.setenv("SMTP_STARTTLS", "false")
    with __import__("pytest").raises(RuntimeError, match="STARTTLS"):
        mailer._send_password_reset_email("recipient@example.com", "x" * 43)


def test_delivery_failure_logs_no_address_token_or_smtp_response(monkeypatch, caplog):
    private_values = ("recipient@example.com", "secret-reset-token", "smtp-auth-detail")

    def fail(_email, _token):
        raise RuntimeError(private_values[2])

    monkeypatch.setattr(mailer, "_send_password_reset_email", fail)
    with caplog.at_level(logging.ERROR, logger=mailer.logger.name):
        mailer.deliver_password_reset_email(private_values[0], private_values[1])

    assert "error_type=RuntimeError" in caplog.text
    assert all(value not in caplog.text for value in private_values)
