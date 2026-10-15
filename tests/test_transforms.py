from __future__ import annotations

import pytest
from pydantic import ValidationError

from price_monitor.transforms import (
    Contains,
    Map,
    Price,
    Regex,
    Strip,
    ToInt,
    TransformError,
    apply_transforms,
    parse_number,
)


@pytest.mark.parametrize(
    ("text", "locale", "expected"),
    [
        ("£51.77", "en_GB", 51.77),
        ("Â£51.77", "en", 51.77),
        ("$1,299.00", "en_US", 1299.0),
        ("1.299,00 €", "de_DE", 1299.0),
        ("12,50 EUR", "es", 12.5),
        ("1 299,90 €", "fr_FR", 1299.9),
        ("1\u202f299,90", "fr", 1299.9),
        ("from 7 only", "en", 7.0),
    ],
)
def test_parse_number(text: str, locale: str, expected: float) -> None:
    assert parse_number(text, locale) == expected


def test_parse_number_without_digits() -> None:
    with pytest.raises(TransformError):
        parse_number("free", "en")


def test_price_currency_check() -> None:
    price = Price(locale="en_GB", currency="gbp")
    assert price.apply("£3.50") == 3.5
    assert price.apply("GBP 3.50") == 3.5
    with pytest.raises(TransformError, match="expected currency GBP"):
        price.apply("$3.50")


def test_price_unknown_currency_requires_code() -> None:
    assert Price(currency="SEK").apply("99 SEK") == 99.0
    with pytest.raises(TransformError):
        Price(currency="SEK").apply("99")


def test_price_rejects_unknown_locale() -> None:
    with pytest.raises(ValidationError, match="unsupported locale"):
        Price(locale="xx_XX")


@pytest.mark.parametrize(("text", "expected"), [("19", 19), ("1,234 units", 1234), ("-5", -5)])
def test_to_int(text: str, expected: int) -> None:
    assert ToInt().apply(text) == expected


def test_to_int_other_types_and_errors() -> None:
    assert ToInt().apply(4.9) == 4
    assert ToInt().apply(True) == 1
    with pytest.raises(TransformError):
        ToInt().apply("none")


def test_regex_group_defaults() -> None:
    assert Regex(pattern=r"\((\d+) available\)").apply("In stock (22 available)") == "22"
    assert Regex(pattern=r"\d+").apply("abc 42") == "42"
    assert Regex(pattern=r"(?P<n>\d+)", group="n").apply("x9") == "9"


def test_regex_errors() -> None:
    with pytest.raises(TransformError, match="did not match"):
        Regex(pattern=r"\d").apply("none")
    with pytest.raises(TransformError, match="does not exist"):
        Regex(pattern=r"(\d)", group=3).apply("1")
    with pytest.raises(ValidationError, match="invalid regex"):
        Regex(pattern="(")


def test_map_and_default() -> None:
    mapping = Map(values={"One": 1, "Two": 2})
    assert mapping.apply(" Two ") == 2
    with pytest.raises(TransformError):
        mapping.apply("Nine")
    assert Map(values={"a": 1}, default=0).apply("zzz") == 0


def test_contains_and_strip_chain() -> None:
    assert Contains(text="in stock").apply("In Stock (3)") is True
    assert Contains(text="in stock", case_sensitive=True).apply("In Stock") is False
    assert apply_transforms("  x ", [Strip()]) == "x"
