"""Command line interface."""

from __future__ import annotations

import logging
import random
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from price_monitor import __version__
from price_monitor.alerts import Alert, AlertEngine, NotifierError, build_notifiers
from price_monitor.config import AppConfig, ConfigError, SiteConfig, load_config
from price_monitor.crawl import CrawlError
from price_monitor.fetch import FetchError
from price_monitor.logging_setup import configure_logging
from price_monitor.models import Change, ChangeKind
from price_monitor.parse import load_parser
from price_monitor.report import load_report_data, write_excel, write_products_csv
from price_monitor.scheduler import install_signal_handlers, parse_interval, watch_loop
from price_monitor.service import run_site
from price_monitor.settings import Settings
from price_monitor.storage import Repository, SchemaError

log = logging.getLogger(__name__)

EXIT_FAILURE = 1
EXIT_USAGE = 2

app = typer.Typer(
    name="price-monitor",
    help="Scrape product pages, keep a price history, detect changes and send alerts.",
    no_args_is_help=True,
    add_completion=False,
)
sites_app = typer.Typer(help="Inspect and validate the site configuration.", no_args_is_help=True)
alerts_app = typer.Typer(help="Test alert delivery.", no_args_is_help=True)
app.add_typer(sites_app, name="sites")
app.add_typer(alerts_app, name="alerts")


class ReportFormat(StrEnum):
    XLSX = "xlsx"
    CSV = "csv"
    BOTH = "both"


@dataclass(slots=True)
class State:
    settings: Settings


def _version(value: bool) -> None:
    if value:
        typer.echo(f"price-monitor {__version__}")
        raise typer.Exit


@app.callback()
def _root(
    ctx: typer.Context,
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Path to sites.yaml.")
    ] = None,
    db: Annotated[Path | None, typer.Option("--db", help="Path to the SQLite database.")] = None,
    log_level: Annotated[str | None, typer.Option(help="DEBUG, INFO, WARNING or ERROR.")] = None,
    log_format: Annotated[str | None, typer.Option(help="text or json.")] = None,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    del version
    try:
        settings = Settings()
        overrides = {
            k: v
            for k, v in {
                "config_path": config,
                "db_path": db,
                "log_level": log_level.upper() if log_level else None,
                "log_format": log_format,
            }.items()
            if v is not None
        }
        settings = Settings.model_validate({**settings.model_dump(), **overrides})
    except ValueError as exc:
        typer.echo(f"error: invalid settings: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE) from exc
    configure_logging(settings.log_level, settings.log_format)
    ctx.obj = State(settings)


def _state(ctx: typer.Context) -> Settings:
    state = ctx.obj
    if not isinstance(state, State):
        raise RuntimeError("CLI state missing; the root callback did not run")
    return state.settings


def _fail(message: str, code: int = EXIT_FAILURE) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(code)


def _load(settings: Settings) -> AppConfig:
    try:
        return load_config(settings.config_path)
    except ConfigError as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc


def _open(settings: Settings) -> Repository:
    try:
        return Repository.open(settings.db_path)
    except (SchemaError, OSError) as exc:
        raise _fail(f"cannot open database {settings.db_path}: {exc}") from exc


def _select(config: AppConfig, names: list[str] | None) -> list[SiteConfig]:
    try:
        return config.selected(names)
    except ConfigError as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc


def _scrape_once(
    sites: list[SiteConfig],
    config: AppConfig,
    repo: Repository,
    settings: Settings,
    *,
    limit_pages: int | None,
    respect_robots: bool,
    simulate: bool,
    send_alerts: bool,
) -> bool:
    """Scrape each site in turn; returns True when every site succeeded."""
    engine = None
    if send_alerts and config.alerts.rules:
        engine = AlertEngine(config.alerts.rules, build_notifiers(settings), repo)
    ok = True
    for site in sites:
        try:
            result = run_site(
                site,
                repo,
                settings,
                limit_pages=limit_pages,
                respect_robots=respect_robots,
                simulate=simulate,
                engine=engine,
            )
        except (CrawlError, FetchError, ConfigError) as exc:
            log.error("site failed", extra={"site": site.name, "error": str(exc)})
            ok = False
            continue
        counts = {kind: sum(1 for c in result.changes if c.kind is kind) for kind in ChangeKind}
        note = (
            "baseline run, no comparison yet"
            if result.baseline
            else ", ".join(f"{counts[k]} {k.value}" for k in ChangeKind)
        )
        typer.echo(f"{site.name}: {result.products} products ({note})")
        if result.failures:
            typer.echo(f"{site.name}: {result.failures} item(s) failed; see log", err=True)
        if result.alerts and result.alerts.matched:
            a = result.alerts
            typer.echo(
                f"{site.name}: alerts matched={a.matched} sent={a.sent} "
                f"duplicate={a.duplicates} failed={a.failed}"
            )
    return ok


SiteOption = Annotated[
    list[str] | None, typer.Option("--site", "-s", help="Site name; repeat for several.")
]
LimitOption = Annotated[
    int | None, typer.Option("--limit-pages", "-n", min=1, help="Only the first N listing pages.")
]
RobotsOption = Annotated[
    bool,
    typer.Option(
        "--ignore-robots",
        help="Do not honour robots.txt. You are responsible for the consequences.",
    ),
]
NoAlertsOption = Annotated[bool, typer.Option("--no-alerts", help="Evaluate no alert rules.")]


@app.command()
def scrape(
    ctx: typer.Context,
    site: SiteOption = None,
    limit_pages: LimitOption = None,
    ignore_robots: RobotsOption = False,
    simulate_changes: Annotated[
        bool,
        typer.Option(
            help="Perturb the scraped data before storing it, to preview change detection. "
            "Writes simulated rows into the database; use a throwaway --db.",
        ),
    ] = False,
    no_alerts: NoAlertsOption = False,
) -> None:
    """Scrape the configured sites once and store the observations."""
    settings = _state(ctx)
    config = _load(settings)
    sites = _select(config, site)
    if simulate_changes:
        typer.echo(
            "warning: --simulate-changes stores altered data; do not use on a real database",
            err=True,
        )
    with _open(settings) as repo:
        ok = _scrape_once(
            sites,
            config,
            repo,
            settings,
            limit_pages=limit_pages,
            respect_robots=not ignore_robots,
            simulate=simulate_changes,
            send_alerts=not no_alerts,
        )
    if not ok:
        raise typer.Exit(EXIT_FAILURE)


@app.command()
def watch(
    ctx: typer.Context,
    site: SiteOption = None,
    interval: Annotated[
        str | None, typer.Option(help="Time between runs, e.g. 30m, 6h, 1d. Default 6h.")
    ] = None,
    jitter: Annotated[
        float | None, typer.Option(min=0.0, max=0.9, help="Random +/- fraction of the interval.")
    ] = None,
    limit_pages: LimitOption = None,
    ignore_robots: RobotsOption = False,
    no_alerts: NoAlertsOption = False,
    report_dir: Annotated[
        Path | None, typer.Option(help="Regenerate the Excel report here after every run.")
    ] = None,
) -> None:
    """Scrape on a schedule until interrupted (SIGINT or SIGTERM)."""
    settings = _state(ctx)
    config = _load(settings)
    sites = _select(config, site)
    try:
        seconds = parse_interval(interval or settings.watch_interval)
    except ValueError as exc:
        raise _fail(str(exc), EXIT_USAGE) from exc
    fraction = settings.watch_jitter if jitter is None else jitter
    stop = threading.Event()
    restore = install_signal_handlers(stop)
    try:
        with _open(settings) as repo:

            def job() -> None:
                _scrape_once(
                    sites,
                    config,
                    repo,
                    settings,
                    limit_pages=limit_pages,
                    respect_robots=not ignore_robots,
                    simulate=False,
                    send_alerts=not no_alerts,
                )
                if report_dir is not None:
                    write_excel(load_report_data(repo), report_dir / "products_report.xlsx")

            log.info("watch started", extra={"interval_seconds": seconds, "jitter": fraction})
            runs = watch_loop(
                job, interval=seconds, jitter=fraction, stop=stop, rng=random.Random()
            )
            log.info("watch stopped", extra={"runs": runs})
    finally:
        restore()


@app.command()
def report(
    ctx: typer.Context,
    site: Annotated[str | None, typer.Option("--site", "-s", help="Limit to one site.")] = None,
    out: Annotated[Path, typer.Option(help="Output directory.")] = Path("reports"),
    fmt: Annotated[
        ReportFormat, typer.Option("--format", help="xlsx, csv or both.")
    ] = ReportFormat.BOTH,
    top: Annotated[int, typer.Option(min=1, max=20, help="Price movers to chart.")] = 5,
) -> None:
    """Write the Excel workbook and/or CSV from stored data."""
    settings = _state(ctx)
    with _open(settings) as repo:
        data = load_report_data(repo, site=site, top_n=top)
    if not data.products:
        raise _fail("the database holds no products yet; run `price-monitor scrape` first")
    if fmt in (ReportFormat.XLSX, ReportFormat.BOTH):
        typer.echo(f"wrote {write_excel(data, out / 'products_report.xlsx')}")
    if fmt in (ReportFormat.CSV, ReportFormat.BOTH):
        typer.echo(f"wrote {write_products_csv(data, out / 'products.csv')}")


@app.command()
def history(
    ctx: typer.Context,
    product: Annotated[str, typer.Argument(help="Product key, or part of its title.")],
    site: Annotated[str | None, typer.Option("--site", "-s", help="Limit to one site.")] = None,
    limit: Annotated[int, typer.Option(min=1, help="Most recent observations to show.")] = 30,
) -> None:
    """Show the price history of one product."""
    settings = _state(ctx)
    with _open(settings) as repo:
        matches = repo.find_products(product, site)
        if not matches:
            raise _fail(f"no product matches {product!r}")
        if len(matches) > 1:
            lines = "\n".join(f"  {m.site}  {m.key}  {m.title}" for m in matches[:10])
            more = f"\n  ... and {len(matches) - 10} more" if len(matches) > 10 else ""
            raise _fail(f"{len(matches)} products match {product!r}; use the key:\n{lines}{more}")
        item = matches[0]
        points = repo.history(item.id)
    typer.echo(f"{item.title}  [{item.site}/{item.key}]")
    typer.echo(f"{'observed (UTC)':<20} {'price':>10}  {'stock':>6}  in stock")
    for p in points[-limit:]:
        stock = "-" if p.stock is None else str(p.stock)
        flag = "-" if p.in_stock is None else ("yes" if p.in_stock else "no")
        typer.echo(f"{p.observed_at:%Y-%m-%d %H:%M:%S}  {p.price:>10.2f}  {stock:>6}  {flag}")
    prices = [p.price for p in points]
    typer.echo(
        f"{len(points)} observation(s); "
        f"min {min(prices):.2f}, max {max(prices):.2f} {item.currency}"
    )


@sites_app.command("validate")
def sites_validate(ctx: typer.Context) -> None:
    """Check sites.yaml: schema, selectors, transforms, alert rules and plugins."""
    settings = _state(ctx)
    config = _load(settings)
    for site in config.sites.values():
        try:
            load_parser(site)
        except ConfigError as exc:
            raise _fail(str(exc), EXIT_USAGE) from exc
        status = "enabled" if site.enabled else "disabled"
        mode = f"plugin {site.plugin}" if site.plugin else f"{len(site.all_fields())} fields"
        typer.echo(f"{site.name}: ok ({status}, {site.renderer} renderer, {mode})")
    typer.echo(
        f"{settings.config_path}: valid, {len(config.sites)} site(s), "
        f"{len(config.alerts.rules)} alert rule(s)"
    )


@alerts_app.command("test")
def alerts_test(
    ctx: typer.Context,
    notifier: Annotated[
        str | None,
        typer.Option(
            "--notifier", "-n", help="telegram, email or webhook (default: all configured)."
        ),
    ] = None,
) -> None:
    """Send a test message through the configured notifiers."""
    settings = _state(ctx)
    available = build_notifiers(settings)
    if notifier is not None:
        if notifier not in available:
            raise _fail(f"notifier {notifier!r} is not configured; see .env.example", EXIT_USAGE)
        available = {notifier: available[notifier]}
    if not available:
        raise _fail("no notifier is configured; see .env.example", EXIT_USAGE)
    from price_monitor.config import NewProductRule

    sample = Alert(
        NewProductRule(name="alerts-test"),
        "test",
        Change(
            ChangeKind.NEW,
            "test",
            "price-monitor test alert",
            "https://example.com",
            "USD",
            new_price=9.99,
        ),
    )
    failed = False
    for name, target in available.items():
        try:
            target.send([sample])
        except NotifierError as exc:
            typer.echo(f"{name}: FAILED ({exc})", err=True)
            failed = True
        else:
            typer.echo(f"{name}: sent")
    if failed:
        raise typer.Exit(EXIT_FAILURE)


def main() -> None:
    app()
