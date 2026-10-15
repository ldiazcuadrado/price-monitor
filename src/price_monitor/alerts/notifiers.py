"""Notifier implementations: Telegram, SMTP email and a generic JSON webhook."""

from __future__ import annotations

import logging
import smtplib
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import httpx

from price_monitor import __version__
from price_monitor.alerts.base import Alert, Notifier, NotifierError, render_digest
from price_monitor.settings import Settings

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def _post_json(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """POST with retries. Errors are re-raised without the URL, which may hold a secret."""
    problem = "no attempt made"
    for attempt in range(attempts):
        try:
            response = client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            problem = type(exc).__name__
        else:
            if response.status_code < 400:
                return
            problem = f"HTTP {response.status_code}"
            if response.status_code not in _RETRY_STATUSES:
                break
        if attempt < attempts - 1:
            sleep(2**attempt)
    raise NotifierError(problem)


def _chunks(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = block[:limit]
    if current:
        chunks.append(current)
    return chunks


class TelegramNotifier:
    name = "telegram"

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._client = client or httpx.Client(timeout=15.0)
        self._sleep = sleep

    def send(self, alerts: Sequence[Alert]) -> None:
        for chunk in _chunks(render_digest(alerts), TELEGRAM_LIMIT):
            _post_json(
                self._client,
                self._url,
                {"chat_id": self._chat_id, "text": chunk, "disable_web_page_preview": True},
                sleep=self._sleep,
            )


class WebhookNotifier:
    name = "webhook"

    def __init__(
        self,
        url: str,
        *,
        bearer_token: str | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
        self._client = client or httpx.Client(timeout=15.0)
        self._sleep = sleep

    def send(self, alerts: Sequence[Alert]) -> None:
        payload = {
            "source": "price-monitor",
            "version": __version__,
            "sent_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "alerts": [a.to_dict() for a in alerts],
        }
        _post_json(self._client, self._url, payload, headers=self._headers, sleep=self._sleep)


class EmailNotifier:
    name = "email"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        recipients: Sequence[str],
        security: str = "starttls",
        username: str | None = None,
        password: str | None = None,
        timeout: float = 20.0,
        smtp_factory: Callable[..., smtplib.SMTP] | None = None,
    ) -> None:
        self._host, self._port = host, port
        self._sender, self._recipients = sender, list(recipients)
        self._security = security
        self._username, self._password = username, password
        self._timeout = timeout
        self._factory = smtp_factory or (smtplib.SMTP_SSL if security == "ssl" else smtplib.SMTP)

    def send(self, alerts: Sequence[Alert]) -> None:
        message = EmailMessage()
        message["Subject"] = f"[price-monitor] {len(alerts)} alert(s)"
        message["From"] = self._sender
        message["To"] = ", ".join(self._recipients)
        message.set_content(render_digest(alerts))
        try:
            with self._factory(self._host, self._port, timeout=self._timeout) as smtp:
                if self._security == "starttls":
                    smtp.starttls()
                if self._username and self._password:
                    smtp.login(self._username, self._password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise NotifierError(type(exc).__name__) from exc


def build_notifiers(settings: Settings) -> dict[str, Notifier]:
    """Instantiate the notifiers whose settings are complete."""
    notifiers: dict[str, Notifier] = {}
    if settings.telegram_bot_token and settings.telegram_chat_id:
        notifiers["telegram"] = TelegramNotifier(
            settings.telegram_bot_token.get_secret_value(), settings.telegram_chat_id
        )
    elif settings.telegram_bot_token or settings.telegram_chat_id:
        log.warning("telegram needs both a bot token and a chat id; notifier disabled")
    if settings.smtp_host and settings.smtp_from and settings.smtp_to:
        notifiers["email"] = EmailNotifier(
            host=settings.smtp_host,
            port=settings.smtp_port,
            sender=settings.smtp_from,
            recipients=settings.smtp_to,
            security=settings.smtp_security,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
        )
    elif settings.smtp_host:
        log.warning("email needs smtp_host, smtp_from and smtp_to; notifier disabled")
    if settings.webhook_url:
        notifiers["webhook"] = WebhookNotifier(
            settings.webhook_url,
            bearer_token=(
                settings.webhook_bearer_token.get_secret_value()
                if settings.webhook_bearer_token
                else None
            ),
        )
    return notifiers
