"""Resolution of the parser used for a site: the generic one or a user plugin."""

from __future__ import annotations

import importlib
from typing import Any

from price_monitor.config import ConfigError, SiteConfig
from price_monitor.parse.base import SiteParser
from price_monitor.parse.generic import GenericParser


def load_parser(site: SiteConfig) -> SiteParser:
    """Return the parser for ``site``.

    A plugin is referenced as ``package.module:factory`` where ``factory`` is called with
    the ``SiteConfig`` and returns an object implementing ``SiteParser`` (a class works).
    """
    if not site.plugin:
        return GenericParser(site)
    module_name, _, attribute = site.plugin.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigError(
            f"site {site.name!r}: cannot import plugin module {module_name!r}: {exc}"
        ) from exc
    try:
        factory: Any = getattr(module, attribute)
    except AttributeError as exc:
        raise ConfigError(
            f"site {site.name!r}: {module_name!r} has no attribute {attribute!r}"
        ) from exc
    parser = factory(site)
    if not (hasattr(parser, "parse_listing") and hasattr(parser, "parse_detail")):
        raise ConfigError(f"site {site.name!r}: plugin must provide parse_listing and parse_detail")
    return parser  # type: ignore[no-any-return]
