"""Alert rules, deduplication and notifiers."""

from price_monitor.alerts.base import Alert, Notifier, NotifierError
from price_monitor.alerts.engine import AlertEngine, DispatchSummary
from price_monitor.alerts.notifiers import (
    EmailNotifier,
    TelegramNotifier,
    WebhookNotifier,
    build_notifiers,
)

__all__ = [
    "Alert",
    "AlertEngine",
    "DispatchSummary",
    "EmailNotifier",
    "Notifier",
    "NotifierError",
    "TelegramNotifier",
    "WebhookNotifier",
    "build_notifiers",
]
