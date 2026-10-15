from __future__ import annotations

import json
import smtplib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, cast

import httpx
import pytest
import respx
from pydantic import TypeAdapter

from price_monitor.alerts import (
    Alert,
    AlertEngine,
    EmailNotifier,
    NotifierError,
    TelegramNotifier,
    WebhookNotifier,
    build_notifiers,
)
from price_monitor.alerts.notifiers import _chunks
from price_monitor.alerts.rules import matches
from price_monitor.config import (
    BackInStockRule,
    BelowTargetRule,
    NewProductRule,
    OutOfStockRule,
    PriceDropRule,
    PriceIncreaseRule,
    RemovedProductRule,
    Rule,
)
from price_monitor.models import Change, ChangeKind
from price_monitor.settings import Settings
from price_monitor.storage import Repository

NOW = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


def price_change(old: float, new: float, title: str = "Blue Lamp") -> Change:
    return Change(ChangeKind.PRICE_CHANGE, "k1", title, "https://x/k1", "EUR", old, new)


def other(kind: ChangeKind) -> Change:
    return Change(kind, "k1", "Blue Lamp", "https://x/k1", "EUR", 10.0, 10.0)


def rule(data: dict[str, Any]) -> Rule:
    return TypeAdapter(Rule).validate_python(data)


class RecordingNotifier:
    def __init__(self, name: str = "rec", fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.batches: list[list[Alert]] = []

    def send(self, alerts: Sequence[Alert]) -> None:
        if self.fail:
            raise NotifierError("boom")
        self.batches.append(list(alerts))


@pytest.mark.parametrize(
    ("spec", "change", "expected"),
    [
        ({"type": "price_drop", "min_percent": 10}, price_change(100, 85), True),
        ({"type": "price_drop", "min_percent": 10}, price_change(100, 95), False),
        ({"type": "price_drop", "min_percent": 10}, price_change(100, 120), False),
        ({"type": "price_drop", "min_absolute": 5}, price_change(100, 96), False),
        ({"type": "price_drop", "min_absolute": 5, "min_percent": 3}, price_change(100, 90), True),
        ({"type": "price_drop"}, price_change(100, 99.9), True),
        ({"type": "price_drop"}, other(ChangeKind.NEW), False),
        ({"type": "price_increase", "min_percent": 10}, price_change(100, 120), True),
        ({"type": "price_increase", "min_percent": 10}, price_change(100, 85), False),
        ({"type": "below_target", "target": 50}, price_change(60, 45), True),
        ({"type": "below_target", "target": 50}, price_change(40, 45), False),
        ({"type": "below_target", "target": 50}, price_change(60, 55), False),
        (
            {"type": "below_target", "target": 50},
            Change(ChangeKind.NEW, "k", "t", "u", "EUR", new_price=20.0),
            True,
        ),
        (
            {"type": "below_target", "target": 50},
            Change(ChangeKind.NEW, "k", "t", "u", "EUR", new_price=70.0),
            False,
        ),
        ({"type": "below_target", "target": 50}, other(ChangeKind.OUT_OF_STOCK), False),
        ({"type": "back_in_stock"}, other(ChangeKind.BACK_IN_STOCK), True),
        ({"type": "back_in_stock"}, other(ChangeKind.OUT_OF_STOCK), False),
        ({"type": "out_of_stock"}, other(ChangeKind.OUT_OF_STOCK), True),
        ({"type": "new_product"}, other(ChangeKind.NEW), True),
        ({"type": "removed_product"}, other(ChangeKind.REMOVED), True),
        ({"type": "removed_product"}, other(ChangeKind.NEW), False),
    ],
)
def test_rule_matching(spec: dict[str, Any], change: Change, expected: bool) -> None:
    assert matches(rule({"name": "r", **spec}), "shop", change) is expected


def test_rule_site_and_title_filters() -> None:
    scoped = rule({"name": "r", "type": "price_drop", "sites": ["a"], "title_regex": "lamp"})
    assert matches(scoped, "a", price_change(10, 5))
    assert not matches(scoped, "b", price_change(10, 5))
    assert not matches(scoped, "a", price_change(10, 5, title="Red Chair"))


def test_rule_classes_are_distinct() -> None:
    kinds = {
        PriceDropRule,
        PriceIncreaseRule,
        BelowTargetRule,
        BackInStockRule,
        OutOfStockRule,
        NewProductRule,
        RemovedProductRule,
    }
    assert len(kinds) == 7


def test_alert_text_and_payload() -> None:
    alert = Alert(rule({"name": "drop", "type": "price_drop"}), "shop", price_change(100, 80))
    assert "Price drop: Blue Lamp: 100.00 EUR -> 80.00 EUR (-20.0%)" in alert.text()
    assert "[drop, shop]" in alert.text()
    payload = alert.to_dict()
    assert payload["percent_change"] == -20.0
    assert payload["kind"] == "price_change"
    assert alert.fingerprint == "drop|shop|k1|price_change|100.0000|80.0000"


@pytest.mark.parametrize(
    ("kind", "fragment"),
    [
        (ChangeKind.NEW, "New product"),
        (ChangeKind.REMOVED, "Removed"),
        (ChangeKind.BACK_IN_STOCK, "Back in stock"),
        (ChangeKind.OUT_OF_STOCK, "Out of stock"),
    ],
)
def test_alert_text_per_kind(kind: ChangeKind, fragment: str) -> None:
    assert fragment in Alert(rule({"name": "r", "type": "new_product"}), "s", other(kind)).text()
    assert (
        "increase"
        in Alert(rule({"name": "r", "type": "new_product"}), "s", price_change(1, 2)).text()
    )


def make_engine(
    repo: Repository, *notifiers: RecordingNotifier, rules: list[Rule] | None = None
) -> AlertEngine:
    return AlertEngine(
        rules or [rule({"name": "drop", "type": "price_drop", "min_percent": 10})],
        {n.name: n for n in notifiers},
        repo,
        clock=lambda: NOW,
    )


def test_engine_sends_one_digest_and_dedups(repo: Repository) -> None:
    notifier = RecordingNotifier()
    engine = make_engine(repo, notifier)
    changes = [price_change(100, 80), price_change(100, 99)]
    first = engine.process("shop", changes)
    assert (first.matched, first.sent, first.duplicates) == (1, 1, 0)
    assert len(notifier.batches) == 1 and len(notifier.batches[0]) == 1

    second = engine.process("shop", changes)
    assert (second.sent, second.duplicates) == (0, 1)
    assert len(notifier.batches) == 1


def test_engine_realerts_after_cooldown(repo: Repository) -> None:
    notifier = RecordingNotifier()
    change = [price_change(100, 80)]
    make_engine(repo, notifier).process("shop", change)
    later = AlertEngine(
        [rule({"name": "drop", "type": "price_drop", "cooldown_hours": 1})],
        {"rec": notifier},
        repo,
        clock=lambda: NOW + timedelta(hours=2),
    )
    assert later.process("shop", change).sent == 1


def test_engine_dedup_is_per_notifier(repo: Repository) -> None:
    a, b = RecordingNotifier("a"), RecordingNotifier("b", fail=True)
    engine = make_engine(repo, a, b)
    summary = engine.process("shop", [price_change(100, 80)])
    assert (summary.sent, summary.failed) == (1, 1)
    b.fail = False
    retry = engine.process("shop", [price_change(100, 80)])
    assert (retry.sent, retry.duplicates) == (1, 1)
    assert len(a.batches) == 1 and len(b.batches) == 1


def test_engine_respects_rule_notify_list(repo: Repository) -> None:
    a, b = RecordingNotifier("a"), RecordingNotifier("b")
    engine = make_engine(
        repo, a, b, rules=[rule({"name": "r", "type": "price_drop", "notify": ["webhook"]})]
    )
    assert engine.process("shop", [price_change(100, 50)]).sent == 0
    engine = make_engine(repo, a, b, rules=[rule({"name": "r2", "type": "price_drop"})])
    assert engine.process("shop", [price_change(100, 50)]).sent == 2


def test_engine_without_notifiers_or_matches(repo: Repository) -> None:
    assert make_engine(repo).process("shop", [price_change(100, 50)]).sent == 0
    assert (
        make_engine(repo, RecordingNotifier()).process("shop", [price_change(100, 99)]).matched == 0
    )


# notifiers ---------------------------------------------------------------------


def alert() -> Alert:
    return Alert(rule({"name": "drop", "type": "price_drop"}), "shop", price_change(100, 80))


@respx.mock
def test_telegram_posts_text_without_leaking_token() -> None:
    route = respx.post("https://api.telegram.org/bot123:SECRET/sendMessage").respond(
        200, json={"ok": True}
    )
    TelegramNotifier("123:SECRET", "42").send([alert()])
    body = json.loads(route.calls.last.request.content)
    assert body["chat_id"] == "42"
    assert "Price drop" in body["text"]


@respx.mock
def test_telegram_failure_message_has_no_url() -> None:
    respx.post("https://api.telegram.org/bot123:SECRET/sendMessage").respond(401)
    with pytest.raises(NotifierError) as info:
        TelegramNotifier("123:SECRET", "42", sleep=lambda _: None).send([alert()])
    assert "SECRET" not in str(info.value)
    assert str(info.value) == "HTTP 401"


@respx.mock
def test_telegram_retries_server_errors() -> None:
    route = respx.post("https://api.telegram.org/bott/sendMessage").mock(
        side_effect=[httpx.Response(502), httpx.ConnectError("x"), httpx.Response(200)]
    )
    sleeps: list[float] = []
    TelegramNotifier("t", "1", sleep=sleeps.append).send([alert()])
    assert route.call_count == 3
    assert sleeps == [1, 2]


@respx.mock
def test_telegram_gives_up() -> None:
    respx.post("https://api.telegram.org/bott/sendMessage").mock(
        side_effect=httpx.ConnectError("x")
    )
    with pytest.raises(NotifierError, match="ConnectError"):
        TelegramNotifier("t", "1", sleep=lambda _: None).send([alert()])


@respx.mock
def test_telegram_splits_long_digests() -> None:
    route = respx.post("https://api.telegram.org/bott/sendMessage").respond(200)
    TelegramNotifier("t", "1").send([alert()] * 120)
    assert route.call_count > 1
    assert all(len(json.loads(c.request.content)["text"]) <= 4000 for c in route.calls)


def test_chunks_truncates_oversized_block() -> None:
    assert _chunks("x" * 50, 10) == ["x" * 10]
    assert _chunks("a\n\nb", 100) == ["a\n\nb"]


@respx.mock
def test_webhook_payload_and_auth() -> None:
    route = respx.post("https://hooks.test/in").respond(204)
    WebhookNotifier("https://hooks.test/in", bearer_token="tok").send([alert()])
    request = route.calls.last.request
    body = json.loads(request.content)
    assert request.headers["authorization"] == "Bearer tok"
    assert body["source"] == "price-monitor"
    assert body["alerts"][0]["rule"] == "drop"


@respx.mock
def test_webhook_without_token_sends_no_auth_header() -> None:
    route = respx.post("https://hooks.test/in").respond(200)
    WebhookNotifier("https://hooks.test/in").send([alert()])
    assert "authorization" not in route.calls.last.request.headers


class FakeSMTP:
    instances: ClassVar[list[FakeSMTP]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.sent: Any = None
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}")

    def send_message(self, message: Any) -> None:
        self.sent = message


FAKE_SMTP = cast("Callable[..., smtplib.SMTP]", FakeSMTP)


def test_email_starttls_flow() -> None:
    FakeSMTP.instances.clear()
    notifier = EmailNotifier(
        host="smtp.test",
        port=587,
        sender="bot@test",
        recipients=["a@test", "b@test"],
        username="user",
        password="pw",
        smtp_factory=FAKE_SMTP,
    )
    notifier.send([alert()])
    smtp = FakeSMTP.instances[0]
    assert smtp.calls == ["starttls", "login:user"]
    assert smtp.sent["To"] == "a@test, b@test"
    assert smtp.sent["Subject"] == "[price-monitor] 1 alert(s)"
    assert "Price drop" in smtp.sent.get_content()


def test_email_without_security_or_login() -> None:
    FakeSMTP.instances.clear()
    EmailNotifier(
        host="h",
        port=25,
        sender="s@t",
        recipients=["r@t"],
        security="none",
        smtp_factory=FAKE_SMTP,
    ).send([alert()])
    assert FakeSMTP.instances[0].calls == []


def test_email_errors_become_notifier_errors() -> None:
    def broken(*args: object, **kwargs: object) -> smtplib.SMTP:
        raise ConnectionRefusedError("nope")

    notifier = EmailNotifier(host="h", port=25, sender="s", recipients=["r"], smtp_factory=broken)
    with pytest.raises(NotifierError, match="ConnectionRefusedError"):
        notifier.send([alert()])


def test_build_notifiers_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="t",
        telegram_chat_id="1",
        smtp_host="h",
        smtp_from="a@b",
        smtp_to="x@y, z@w",
        smtp_password="pw",
        webhook_url="https://hooks.test",
        webhook_bearer_token="b",
    )
    assert settings.smtp_to == ["x@y", "z@w"]
    assert set(build_notifiers(settings)) == {"telegram", "email", "webhook"}


def test_build_notifiers_ignores_incomplete_settings() -> None:
    settings = Settings(_env_file=None, telegram_bot_token="t", smtp_host="h")
    assert build_notifiers(settings) == {}
    assert build_notifiers(Settings(_env_file=None)) == {}
