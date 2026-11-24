"""SMTP delivery for one-time password reset links."""

import html
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote, urlsplit

logger = logging.getLogger("preco_bao.password_reset_email")


def _required_setting(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def _reset_link(token: str) -> str:
    reset_url = _required_setting("PASSWORD_RESET_URL")
    parsed = urlsplit(reset_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not parsed.path.endswith("/reset-password")
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("PASSWORD_RESET_URL must be an HTTPS reset-password URL")
    # Keep the bearer-like reset token in the fragment: browsers do not send
    # fragments in HTTP requests or Referer headers.
    return f"{reset_url}#token={quote(token, safe='')}"


def _send_password_reset_email(email: str, token: str) -> None:
    if os.environ.get("SMTP_STARTTLS", "true").strip().casefold() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise RuntimeError("SMTP STARTTLS must be enabled")

    host = _required_setting("SMTP_HOST")
    port = int(_required_setting("SMTP_PORT"))
    username = _required_setting("SMTP_USERNAME")
    password = _required_setting("SMTP_PASSWORD")
    from_email = _required_setting("SMTP_FROM_EMAIL")
    from_name = os.environ.get("SMTP_FROM_NAME", "Preço Bão").strip() or "Preço Bão"
    timeout = float(os.environ.get("SMTP_TIMEOUT_SECONDS", "10"))
    if not 1 <= timeout <= 60:
        raise RuntimeError("SMTP_TIMEOUT_SECONDS must be between 1 and 60")

    link = _reset_link(token)
    safe_link = html.escape(link, quote=True)
    message = EmailMessage()
    message["Subject"] = "Recuperação de senha — Preço Bão"
    message["From"] = f"{from_name} <{from_email}>"
    message["To"] = email
    message.set_content(
        "Olá!\n\n"
        "Recebemos uma solicitação para redefinir "
        "a senha da sua conta no Preço Bão.\n\n"
        "Clique no botão abaixo para cadastrar uma nova senha.\n\n"
        f"Redefina sua senha neste link: {link}\n\n"
        "O link expira em 20 minutos.\n\n"
        "Se você não solicitou esta alteração, pode ignorar esta mensagem."
    )
    message.add_alternative(
        '<!doctype html><html lang="pt-BR"><body style="margin:0;background:#f6f8fc;'
        'font-family:Arial,sans-serif;color:#172033"><main style="max-width:560px;'
        "margin:24px auto;padding:28px;background:#fff;border:1px solid #d9e6f7;"
        'border-radius:16px"><h1 style="font-size:24px">Recuperação de senha</h1>'
        "<p>Olá!</p><p>Recebemos uma solicitação para redefinir "
        "a senha da sua conta no Preço Bão.</p>"
        "<p>Clique no botão abaixo para cadastrar uma nova senha.</p>"
        f'<p><a href="{safe_link}" style="display:inline-block;padding:14px 22px;'
        'background:#1688f6;color:#fff;text-decoration:none;border-radius:10px;font-weight:bold">'
        "Redefinir minha senha</a></p><p>Este link expira em 20 minutos.</p>"
        "<p>Se você não solicitou esta alteração, pode ignorar esta mensagem.</p>"
        "</main></body></html>",
        subtype="html",
    )

    tls_context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=timeout) as smtp:
        smtp.ehlo()
        smtp.starttls(context=tls_context)
        smtp.ehlo()
        smtp.login(username, password)
        refused = smtp.send_message(message)
        if refused:
            raise RuntimeError("SMTP server refused a recipient")


def deliver_password_reset_email(email: str, token: str) -> None:
    """Send without ever logging the address, token, message, or SMTP response."""
    try:
        _send_password_reset_email(email, token)
    except Exception as error:  # Background delivery must not leak SMTP details.
        logger.error(
            "password_reset_email outcome=failed error_type=%s",
            type(error).__name__,
        )
    else:
        logger.info("password_reset_email outcome=sent")
