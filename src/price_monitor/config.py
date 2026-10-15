"""Validated models for ``sites.yaml``."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import soupsieve
import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from price_monitor.transforms import Transform

NotifierName = Literal["telegram", "email", "webhook"]
_PLUGIN_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
CORE_FIELDS = frozenset({"title", "price", "url", "category", "stock", "in_stock"})


class ConfigError(Exception):
    """The configuration file is missing or invalid."""


def _http_url(value: str) -> str:
    if not re.match(r"^https?://[^\s/]+", value):
        raise ValueError(f"not an http(s) URL: {value!r}")
    return value


def _css(value: str) -> str:
    try:
        soupsieve.compile(value)
    except Exception as exc:
        raise ValueError(f"invalid CSS selector {value!r}: {exc}") from exc
    return value


HttpUrlStr = Annotated[str, AfterValidator(_http_url)]
CssStr = Annotated[str, AfterValidator(_css)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldSpec(_Model):
    """How to extract one value: a CSS selector, an optional attribute, then transforms."""

    css: CssStr | None = None
    attr: str | None = None
    transforms: list[Transform] = Field(default_factory=list)
    default: str | int | float | bool | None = None

    @property
    def has_default(self) -> bool:
        return "default" in self.model_fields_set


class NextLinkPagination(_Model):
    type: Literal["next_link"] = "next_link"
    selector: CssStr
    max_pages: int = Field(default=200, ge=1)


class UrlTemplatePagination(_Model):
    type: Literal["url_template"] = "url_template"
    template: HttpUrlStr
    first_page: int = 1
    max_pages: int = Field(default=200, ge=1)

    @field_validator("template")
    @classmethod
    def _has_placeholder(cls, value: str) -> str:
        if "{page}" not in value:
            raise ValueError("template must contain the {page} placeholder")
        return value


Pagination = Annotated[NextLinkPagination | UrlTemplatePagination, Field(discriminator="type")]


class DetailConfig(_Model):
    fields: dict[str, FieldSpec] = Field(min_length=1)


class RequestConfig(_Model):
    delay_seconds: float = Field(default=1.0, ge=0)
    concurrency: int = Field(default=1, ge=1, le=8)
    timeout_seconds: float = Field(default=20.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    backoff_base_seconds: float = Field(default=1.0, gt=0)
    headers: dict[str, str] = Field(default_factory=dict)
    user_agent: str | None = None
    encoding: str | None = None


class BrowserConfig(_Model):
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "domcontentloaded"
    wait_for_selector: CssStr | None = None


class Thresholds(_Model):
    """Price changes smaller than every configured threshold are ignored."""

    min_percent: float | None = Field(default=None, ge=0)
    min_absolute: float | None = Field(default=None, ge=0)


class SiteConfig(_Model):
    name: str = ""
    enabled: bool = True
    start_urls: list[HttpUrlStr] = Field(min_length=1)
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    key: str = "url"
    renderer: Literal["http", "browser"] = "http"
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    pagination: Pagination | None = None
    item_selector: CssStr | None = None
    fields: dict[str, FieldSpec] = Field(default_factory=dict)
    detail: DetailConfig | None = None
    request: RequestConfig = Field(default_factory=RequestConfig)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    plugin: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @field_validator("plugin")
    @classmethod
    def _plugin_ref(cls, value: str | None) -> str | None:
        if value is not None and not _PLUGIN_RE.match(value):
            raise ValueError("plugin must look like 'package.module:attribute'")
        return value

    @model_validator(mode="after")
    def _check_structure(self) -> Self:
        if self.renderer == "browser" and self.request.concurrency != 1:
            object.__setattr__(self, "request", self.request.model_copy(update={"concurrency": 1}))
        if self.plugin:
            return self
        if not self.item_selector:
            raise ValueError("item_selector is required unless a plugin is configured")
        available = set(self.fields) | set(self.detail.fields if self.detail else ()) | {"url"}
        missing = {"title", "price"} - available
        if missing:
            raise ValueError(f"fields must define: {', '.join(sorted(missing))}")
        if self.key not in available:
            raise ValueError(f"key field {self.key!r} is not defined in fields or detail.fields")
        if self.detail and "url" not in self.fields:
            raise ValueError("detail pages require a 'url' field in fields")
        return self

    def all_fields(self) -> dict[str, FieldSpec]:
        return {**self.fields, **(self.detail.fields if self.detail else {})}


class _RuleBase(_Model):
    name: str = Field(min_length=1)
    sites: list[str] | None = None
    title_regex: str | None = None
    notify: list[NotifierName] | None = None
    cooldown_hours: float = Field(default=168.0, ge=0)

    @field_validator("title_regex")
    @classmethod
    def _compiles(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from exc
        return value


class PriceDropRule(_RuleBase):
    type: Literal["price_drop"] = "price_drop"
    min_percent: float | None = Field(default=None, ge=0)
    min_absolute: float | None = Field(default=None, ge=0)


class PriceIncreaseRule(_RuleBase):
    type: Literal["price_increase"] = "price_increase"
    min_percent: float | None = Field(default=None, ge=0)
    min_absolute: float | None = Field(default=None, ge=0)


class BelowTargetRule(_RuleBase):
    type: Literal["below_target"] = "below_target"
    target: float = Field(gt=0)


class BackInStockRule(_RuleBase):
    type: Literal["back_in_stock"] = "back_in_stock"


class OutOfStockRule(_RuleBase):
    type: Literal["out_of_stock"] = "out_of_stock"


class NewProductRule(_RuleBase):
    type: Literal["new_product"] = "new_product"


class RemovedProductRule(_RuleBase):
    type: Literal["removed_product"] = "removed_product"


Rule = Annotated[
    PriceDropRule
    | PriceIncreaseRule
    | BelowTargetRule
    | BackInStockRule
    | OutOfStockRule
    | NewProductRule
    | RemovedProductRule,
    Field(discriminator="type"),
]


class AlertsConfig(_Model):
    rules: list[Rule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        names = [rule.name for rule in self.rules]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"duplicate rule names: {', '.join(sorted(duplicates))}")
        return self


class AppConfig(_Model):
    sites: dict[str, SiteConfig] = Field(min_length=1)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)

    @model_validator(mode="after")
    def _finalize(self) -> Self:
        for name, site in self.sites.items():
            object.__setattr__(site, "name", name)
        unknown = {
            s for rule in self.alerts.rules for s in (rule.sites or []) if s not in self.sites
        }
        if unknown:
            raise ValueError(f"alert rules reference unknown sites: {', '.join(sorted(unknown))}")
        return self

    def selected(self, names: list[str] | None = None) -> list[SiteConfig]:
        """Enabled sites, or the named ones (which may be disabled)."""
        if not names:
            return [s for s in self.sites.values() if s.enabled]
        unknown = [n for n in names if n not in self.sites]
        if unknown:
            raise ConfigError(f"unknown site(s): {', '.join(unknown)}")
        return [self.sites[n] for n in names]


def format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  {location}: {err['msg']}")
    return "\n".join(lines)


def parse_config(data: Any) -> AppConfig:
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration:\n{format_validation_error(exc)}") from exc


def load_config(path: Path) -> AppConfig:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return parse_config(data)
