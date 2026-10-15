"""One scrape of one site, end to end: crawl, detect changes, store, alert."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from price_monitor.alerts import AlertEngine, DispatchSummary
from price_monitor.changes import detect_changes
from price_monitor.config import SiteConfig
from price_monitor.crawl import crawl_site
from price_monitor.fetch import Fetcher, build_fetcher
from price_monitor.models import Change, ChangeKind
from price_monitor.parse import SiteParser, load_parser
from price_monitor.settings import Settings
from price_monitor.simulate import simulate_changes
from price_monitor.storage import Repository

log = logging.getLogger(__name__)

FetcherFactory = Callable[[SiteConfig, Settings, bool], Fetcher]


def default_fetcher_factory(site: SiteConfig, settings: Settings, respect_robots: bool) -> Fetcher:
    return build_fetcher(site, settings, respect_robots=respect_robots)


@dataclass(slots=True)
class SiteRunResult:
    site: str
    run_id: int
    products: int
    failures: int
    baseline: bool
    simulated: bool
    changes: list[Change] = field(default_factory=list)
    alerts: DispatchSummary | None = None


def run_site(
    site: SiteConfig,
    repo: Repository,
    settings: Settings,
    *,
    limit_pages: int | None = None,
    respect_robots: bool = True,
    simulate: bool = False,
    engine: AlertEngine | None = None,
    fetcher_factory: FetcherFactory = default_fetcher_factory,
    parser: SiteParser | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SiteRunResult:
    """Scrape ``site`` once and persist the outcome.

    The first successful run of a site is a baseline: it records observations but reports no
    changes. Membership changes (new/removed) are only reported when the previous run used the
    same page limit and this run had no failures, otherwise they would be false positives.
    """
    if not respect_robots:
        log.warning("robots.txt checks are disabled for this run", extra={"site": site.name})
    parser = parser or load_parser(site)
    run_id = repo.start_run(site.name, clock(), page_limit=limit_pages, simulated=simulate)
    fetcher = fetcher_factory(site, settings, respect_robots)
    try:
        result = crawl_site(site, fetcher, parser, limit_pages=limit_pages)
    except Exception as exc:
        repo.finish_run(run_id, clock(), status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        fetcher.close()

    products = simulate_changes(result.products, seed=run_id) if simulate else result.products
    previous_run = repo.last_ok_run(site.name, before_run_id=run_id)
    baseline = previous_run is None
    changes: list[Change] = []
    if previous_run is not None:
        track_membership = previous_run.page_limit == limit_pages and result.failures == 0
        if not track_membership:
            log.warning(
                "skipping new/removed detection: page limit changed or the crawl had failures",
                extra={"site": site.name},
            )
        changes = detect_changes(
            repo.latest_states(site.name),
            products,
            site.thresholds,
            track_membership=track_membership,
        )

    now = clock()
    with repo.transaction():
        repo.record_observations(run_id, site.name, products, now)
        removed = [c.key for c in changes if c.kind is ChangeKind.REMOVED]
        repo.deactivate(site.name, removed)
        repo.add_events(run_id, site.name, changes, now)
        repo.finish_run(
            run_id, now, status="ok", products_seen=len(products), failures=result.failures
        )

    summary: DispatchSummary | None = None
    if engine is not None and not simulate:
        summary = engine.process(site.name, changes)
    elif engine is not None:
        would_fire = len(engine.build_alerts(site.name, changes))
        log.warning(
            "simulated run: notifications suppressed",
            extra={"site": site.name, "would_fire": would_fire},
        )
    log.info(
        "run complete",
        extra={
            "site": site.name,
            "run_id": run_id,
            "products": len(products),
            "changes": len(changes),
            "baseline": baseline,
            "failures": result.failures,
        },
    )
    return SiteRunResult(
        site=site.name,
        run_id=run_id,
        products=len(products),
        failures=result.failures,
        baseline=baseline,
        simulated=simulate,
        changes=changes,
        alerts=summary,
    )
