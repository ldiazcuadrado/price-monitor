from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from price_monitor.config import SiteConfig, load_config
from price_monitor.settings import Settings

if TYPE_CHECKING:
    from price_monitor.storage import Repository

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("PRICE_MONITOR_"):
            monkeypatch.delenv(name)


@pytest.fixture
def books_site() -> SiteConfig:
    """The shipped books.toscrape.com configuration with rate limiting switched off."""
    site = load_config(ROOT / "sites.yaml").sites["books-toscrape"]
    request = site.request.model_copy(update={"delay_seconds": 0.0, "backoff_base_seconds": 0.01})
    return site.model_copy(update={"request": request})


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, db_path=tmp_path / "test.db")


@pytest.fixture
def repo() -> Iterator[Repository]:
    from price_monitor.storage import Repository

    repository = Repository.open(":memory:")
    yield repository
    repository.close()
