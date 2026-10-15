"""HTTP and browser fetching with robots.txt, rate limiting and retries."""

from price_monitor.fetch.base import Fetcher, FetchError, RobotsDisallowedError
from price_monitor.fetch.factory import build_fetcher

__all__ = ["FetchError", "Fetcher", "RobotsDisallowedError", "build_fetcher"]
