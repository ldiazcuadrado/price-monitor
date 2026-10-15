from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
import respx

from price_monitor.alerts import AlertEngine
from price_monitor.config import ConfigError, SiteConfig, parse_config
from price_monitor.crawl import CrawlError
from price_monitor.models import ChangeKind, ListingPage, RawItem
from price_monitor.service import SiteRunResult, run_site
from price_monitor.settings import Settings
from price_monitor.simulate import GHOST_PREFIX
from price_monitor.storage import Repository
from tests.helpers import Book, FakeShop
from tests.test_alerts import RecordingNotifier, rule

CATALOGUE = [
    Book("u1", "Alpha", 10.0),
    Book("u2", "Beta", 20.0),
    Book("u3", "Gamma", 30.0),
]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 4, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


def run(
    site: SiteConfig,
    repo: Repository,
    settings: Settings,
    clock: Clock,
    *,
    limit_pages: int | None = None,
    respect_robots: bool = True,
    simulate: bool = False,
    engine: AlertEngine | None = None,
) -> SiteRunResult:
    return run_site(
        site,
        repo,
        settings,
        limit_pages=limit_pages,
        respect_robots=respect_robots,
        simulate=simulate,
        engine=engine,
        clock=clock,
    )


def kinds(result: SiteRunResult) -> list[tuple[str, ChangeKind]]:
    return [(c.key, c.kind) for c in result.changes]


@respx.mock
def test_baseline_then_changes(
    books_site: SiteConfig, settings: Settings, repo: Repository
) -> None:
    shop = FakeShop([list(CATALOGUE)])
    shop.mount(respx)
    clock = Clock()

    first = run(books_site, repo, settings, clock)
    assert first.baseline and first.changes == []
    assert first.products == 3

    shop.pages = [
        [
            dataclasses.replace(CATALOGUE[0], price=12.0),
            dataclasses.replace(CATALOGUE[1], stock=0),
            Book("u4", "Delta", 5.0),
        ]
    ]
    second = run(books_site, repo, settings, clock)
    assert not second.baseline
    assert sorted(kinds(second), key=str) == sorted(
        [
            ("u1", ChangeKind.PRICE_CHANGE),
            ("u2", ChangeKind.OUT_OF_STOCK),
            ("u3", ChangeKind.REMOVED),
            ("u4", ChangeKind.NEW),
        ],
        key=str,
    )
    states = repo.latest_states(books_site.name)
    assert not states["u3"].active
    assert states["u1"].price == 12.0
    assert len(repo.events_for_run(second.run_id)) == 4

    third = run(books_site, repo, settings, clock)
    assert third.changes == []


@respx.mock
def test_identical_run_reports_nothing(
    books_site: SiteConfig, settings: Settings, repo: Repository
) -> None:
    FakeShop([list(CATALOGUE)]).mount(respx)
    clock = Clock()
    run(books_site, repo, settings, clock)
    assert run(books_site, repo, settings, clock).changes == []
    assert len(repo.history(repo.latest_states(books_site.name)["u1"].id)) == 2


@respx.mock
def test_membership_skipped_when_page_limit_differs(
    books_site: SiteConfig, settings: Settings, repo: Repository
) -> None:
    shop = FakeShop([[CATALOGUE[0]], [CATALOGUE[1], CATALOGUE[2]]])
    shop.mount(respx)
    clock = Clock()
    run(books_site, repo, settings, clock)
    shop.pages = [[dataclasses.replace(CATALOGUE[0], price=11.0)], [CATALOGUE[1], CATALOGUE[2]]]
    limited = run(books_site, repo, settings, clock, limit_pages=1)
    assert kinds(limited) == [("u1", ChangeKind.PRICE_CHANGE)]
    assert all(s.active for s in repo.latest_states(books_site.name).values())


@respx.mock
def test_membership_skipped_when_crawl_had_failures(
    books_site: SiteConfig, settings: Settings, repo: Repository
) -> None:
    shop = FakeShop([list(CATALOGUE)])
    shop.mount(respx)
    clock = Clock()
    run(books_site, repo, settings, clock)
    shop.broken.add(CATALOGUE[2].slug)
    result = run(books_site, repo, settings, clock)
    assert result.failures == 1
    assert result.changes == []
    assert repo.latest_states(books_site.name)["u3"].active


@respx.mock
def test_failed_run_is_recorded_and_ignored_as_baseline(
    books_site: SiteConfig, settings: Settings, repo: Repository
) -> None:
    shop = FakeShop([list(CATALOGUE)])
    shop.mount(respx, robots="User-agent: *\nDisallow: /\n")
    clock = Clock()
    with pytest.raises(CrawlError):
        run(books_site, repo, settings, clock)
    assert repo.runs() == []
    shop2 = FakeShop([list(CATALOGUE)])
    respx.clear()
    shop2.mount(respx)
    assert run(books_site, repo, settings, clock).baseline


@respx.mock
def test_ignoring_robots_proceeds(
    books_site: SiteConfig, settings: Settings, repo: Repository
) -> None:
    FakeShop([list(CATALOGUE)]).mount(respx, robots="User-agent: *\nDisallow: /\n")
    result = run(books_site, repo, settings, Clock(), respect_robots=False)
    assert result.products == 3


@respx.mock
def test_unexpected_error_marks_run_failed(
    books_site: SiteConfig, settings: Settings, repo: Repository
) -> None:
    FakeShop([list(CATALOGUE)]).mount(respx)

    class Exploding:
        def parse_listing(self, html: str, url: str, page: int) -> ListingPage:
            raise RuntimeError("parser bug")

        def parse_detail(self, html: str, url: str, item: RawItem) -> RawItem:
            return {}

    with pytest.raises(RuntimeError):
        run_site(books_site, repo, settings, parser=Exploding())


@respx.mock
def test_alerts_fire_once(books_site: SiteConfig, settings: Settings, repo: Repository) -> None:
    shop = FakeShop([list(CATALOGUE)])
    shop.mount(respx)
    notifier = RecordingNotifier()
    engine = AlertEngine(
        [rule({"name": "drop", "type": "price_drop", "min_percent": 10})], {"rec": notifier}, repo
    )
    clock = Clock()
    run(books_site, repo, settings, clock, engine=engine)
    shop.pages = [[dataclasses.replace(CATALOGUE[1], price=10.0), CATALOGUE[0], CATALOGUE[2]]]
    second = run(books_site, repo, settings, clock, engine=engine)
    assert second.alerts is not None and second.alerts.sent == 1
    third = run(books_site, repo, settings, clock, engine=engine)
    assert third.changes == []
    assert len(notifier.batches) == 1


@respx.mock
def test_simulated_runs_do_not_notify(
    books_site: SiteConfig, settings: Settings, repo: Repository
) -> None:
    catalogue = [Book(f"u{i}", f"Book {i}", 10.0 + i) for i in range(30)]
    FakeShop([catalogue]).mount(respx)
    notifier = RecordingNotifier()
    engine = AlertEngine([rule({"name": "any", "type": "new_product"})], {"rec": notifier}, repo)
    clock = Clock()
    run(books_site, repo, settings, clock, engine=engine, simulate=True)
    second = run(books_site, repo, settings, clock, engine=engine, simulate=True)
    assert second.simulated and second.alerts is None
    assert notifier.batches == []
    kinds_seen = {c.kind for c in second.changes}
    assert ChangeKind.PRICE_CHANGE in kinds_seen
    assert any(k.startswith(GHOST_PREFIX) for k in repo.product_ids(books_site.name))
    assert repo.runs(books_site.name)[-1].simulated


def test_unusable_plugin_fails_before_any_run(settings: Settings, repo: Repository) -> None:
    config = parse_config(
        {
            "sites": {
                "p": {
                    "start_urls": ["https://p.test"],
                    "currency": "USD",
                    "plugin": "missing_module_abc:Thing",
                }
            }
        }
    )
    with pytest.raises(ConfigError):
        run_site(config.sites["p"], repo, settings)
    assert repo.runs() == []
