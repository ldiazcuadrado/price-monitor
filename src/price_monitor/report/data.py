"""Report inputs, read from the database."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from price_monitor.models import ChangeKind
from price_monitor.storage import EventRecord, HistoryPoint, ProductState, Repository, RunRecord

_KIND_ORDER = {
    ChangeKind.PRICE_CHANGE: 0,
    ChangeKind.OUT_OF_STOCK: 1,
    ChangeKind.BACK_IN_STOCK: 2,
    ChangeKind.NEW: 3,
    ChangeKind.REMOVED: 4,
}


@dataclass(frozen=True, slots=True)
class Mover:
    product: ProductState
    first_price: float
    last_price: float
    series: list[HistoryPoint]

    @property
    def percent(self) -> float:
        return (
            (self.last_price - self.first_price) / self.first_price * 100
            if self.first_price
            else 0.0
        )


@dataclass(slots=True)
class ReportData:
    generated_at: datetime
    products: list[ProductState]
    runs: list[RunRecord]
    events: list[EventRecord]
    history: list[tuple[ProductState, HistoryPoint]]
    movers: list[Mover]
    baseline_sites: list[str] = field(default_factory=list)


def top_movers(history: list[tuple[ProductState, HistoryPoint]], limit: int) -> list[Mover]:
    """Products whose price changed over time, ranked by absolute relative change."""
    series: dict[int, list[HistoryPoint]] = defaultdict(list)
    products: dict[int, ProductState] = {}
    for product, point in history:
        series[product.id].append(point)
        products[product.id] = product
    movers = []
    for pid, points in series.items():
        prices = {p.price for p in points}
        if len(prices) < 2:
            continue
        movers.append(Mover(products[pid], points[0].price, points[-1].price, points))
    movers.sort(key=lambda m: (abs(m.percent), abs(m.last_price - m.first_price)), reverse=True)
    return movers[:limit]


def load_report_data(repo: Repository, *, site: str | None = None, top_n: int = 5) -> ReportData:
    runs = repo.runs(site)
    latest_by_site: dict[str, RunRecord] = {}
    runs_by_site: dict[str, int] = defaultdict(int)
    for run in runs:
        latest_by_site[run.site] = run
        runs_by_site[run.site] += 1
    events: list[EventRecord] = []
    for latest in latest_by_site.values():
        events.extend(repo.events_for_run(latest.id))
    events.sort(
        key=lambda e: (e.site, _KIND_ORDER[e.kind], -abs((e.new_price or 0) - (e.old_price or 0)))
    )
    history = repo.all_observations(site)
    return ReportData(
        generated_at=datetime.now(UTC),
        products=repo.current_products(site),
        runs=runs,
        events=events,
        history=history,
        movers=top_movers(history, top_n),
        baseline_sites=sorted(s for s, n in runs_by_site.items() if n == 1),
    )
