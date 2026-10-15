"""HTML parsing: generic selector-driven parser and the plugin hook."""

from price_monitor.parse.base import ParseError, SiteParser
from price_monitor.parse.generic import GenericParser
from price_monitor.parse.plugin import load_parser
from price_monitor.parse.product import to_product

__all__ = ["GenericParser", "ParseError", "SiteParser", "load_parser", "to_product"]
