"""Value transforms applied to extracted field text, declared in ``sites.yaml``."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# locale language -> (decimal separator, thousands separator pattern)
_NUMBER_FORMATS: dict[str, tuple[str, str]] = {
    **dict.fromkeys(("en", "ja", "zh", "ko", "he", "th"), (".", ",")),
    **dict.fromkeys(("de", "es", "it", "nl", "pt", "tr", "id", "da", "el", "ro"), (",", ".")),
    **dict.fromkeys(
        ("fr", "ru", "pl", "cs", "sv", "nb", "fi", "uk", "sk", "hu"), (",", r"[\s\u00a0\u202f]")
    ),
}

_CURRENCY_MARKERS: dict[str, tuple[str, ...]] = {
    "GBP": ("£", "GBP"),
    "EUR": ("€", "EUR"),
    "USD": ("$", "USD"),
    "JPY": ("¥", "JPY"),
    "CHF": ("CHF", "Fr."),
}


class TransformError(ValueError):
    """The value could not be transformed."""


def _language(locale: str) -> str:
    return locale.replace("-", "_").split("_")[0].lower()


def parse_number(text: str, locale: str = "en") -> float:
    """Extract the first number from ``text`` using the separators of ``locale``."""
    decimal, thousands = _NUMBER_FORMATS[_language(locale)]
    group = thousands if thousands.startswith("[") else re.escape(thousands)
    pattern = rf"\d(?:(?:{group}|{re.escape(decimal)}|\d)*\d)?"
    match = re.search(pattern, text)
    if not match:
        raise TransformError(f"no number found in {text!r}")
    token = re.sub(group, "", match.group()).replace(decimal, ".")
    try:
        return float(token)
    except ValueError as exc:
        raise TransformError(f"cannot parse number from {text!r}") from exc


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Strip(_Base):
    type: Literal["strip"] = "strip"

    def apply(self, value: Any) -> Any:
        return str(value).strip()


class ToInt(_Base):
    type: Literal["int"] = "int"

    def apply(self, value: Any) -> int:
        if isinstance(value, int | float):
            return int(value)
        match = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
        if not match:
            raise TransformError(f"no integer found in {value!r}")
        return int(float(match.group().replace(",", "")))


class Price(_Base):
    type: Literal["price"] = "price"
    locale: str = "en"
    currency: str | None = None

    @field_validator("locale")
    @classmethod
    def _known_locale(cls, value: str) -> str:
        if _language(value) not in _NUMBER_FORMATS:
            raise ValueError(f"unsupported locale {value!r}; known: {sorted(_NUMBER_FORMATS)}")
        return value

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    def apply(self, value: Any) -> float:
        text = str(value)
        if self.currency:
            markers = _CURRENCY_MARKERS.get(self.currency, (self.currency,))
            if not any(marker in text for marker in markers):
                raise TransformError(f"expected currency {self.currency} in {text!r}")
        return parse_number(text, self.locale)


class Regex(_Base):
    type: Literal["regex"] = "regex"
    pattern: str
    group: int | str | None = None

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
        return value

    def apply(self, value: Any) -> str:
        match = re.search(self.pattern, str(value))
        if not match:
            raise TransformError(f"pattern {self.pattern!r} did not match {value!r}")
        group = self.group if self.group is not None else (1 if match.re.groups else 0)
        try:
            return match.group(group)
        except IndexError as exc:
            raise TransformError(f"regex group {group!r} does not exist") from exc


class Map(_Base):
    type: Literal["map"] = "map"
    values: dict[str, str | int | float | bool]
    default: str | int | float | bool | None = None

    def apply(self, value: Any) -> Any:
        text = str(value).strip()
        if text in self.values:
            return self.values[text]
        if self.default is not None:
            return self.default
        raise TransformError(f"{text!r} is not in the mapping")


class Contains(_Base):
    type: Literal["contains"] = "contains"
    text: str
    case_sensitive: bool = False

    def apply(self, value: Any) -> bool:
        haystack, needle = str(value), self.text
        if not self.case_sensitive:
            haystack, needle = haystack.lower(), needle.lower()
        return needle in haystack


Transform = Annotated[
    Strip | ToInt | Price | Regex | Map | Contains,
    Field(discriminator="type"),
]


def apply_transforms(value: Any, transforms: list[Transform]) -> Any:
    for transform in transforms:
        value = transform.apply(value)
    return value
