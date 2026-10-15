"""Turns changes into alerts, suppresses duplicates and hands them to notifiers."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from price_monitor.alerts.base import Alert, Notifier, NotifierError
from price_monitor.alerts.rules import matches
from price_monitor.config import Rule
from price_monitor.models import Change
from price_monitor.storage import Repository

log = logging.getLogger(__name__)


@dataclass(slots=True)
class DispatchSummary:
    matched: int = 0
    sent: int = 0
    duplicates: int = 0
    failed: int = 0


class AlertEngine:
    def __init__(
        self,
        rules: Sequence[Rule],
        notifiers: Mapping[str, Notifier],
        repo: Repository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._rules = list(rules)
        self._notifiers = dict(notifiers)
        self._repo = repo
        self._clock = clock

    def build_alerts(self, site: str, changes: Sequence[Change]) -> list[Alert]:
        return [
            Alert(rule, site, change)
            for change in changes
            for rule in self._rules
            if matches(rule, site, change)
        ]

    def process(self, site: str, changes: Sequence[Change]) -> DispatchSummary:
        alerts = self.build_alerts(site, changes)
        summary = DispatchSummary(matched=len(alerts))
        if not alerts:
            return summary
        if not self._notifiers:
            log.warning(
                "alerts matched but no notifier is configured", extra={"matched": len(alerts)}
            )
            return summary
        now = self._clock()
        for name, notifier in self._notifiers.items():
            pending: list[Alert] = []
            for alert in alerts:
                if alert.rule.notify is not None and name not in alert.rule.notify:
                    continue
                since = now - timedelta(hours=alert.rule.cooldown_hours)
                if self._repo.alert_sent_since(alert.fingerprint, name, since):
                    summary.duplicates += 1
                else:
                    pending.append(alert)
            if not pending:
                continue
            try:
                notifier.send(pending)
            except NotifierError as exc:
                summary.failed += len(pending)
                log.error(
                    "notifier failed",
                    extra={"notifier": name, "error": str(exc), "alerts": len(pending)},
                )
                continue
            for alert in pending:
                self._repo.record_alert(alert.fingerprint, name, alert.rule.name, now)
            summary.sent += len(pending)
            log.info("alerts delivered", extra={"notifier": name, "alerts": len(pending)})
        return summary
