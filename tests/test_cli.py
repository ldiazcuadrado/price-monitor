from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx
from openpyxl import load_workbook
from typer.testing import CliRunner, Result

from price_monitor import cli
from price_monitor.cli import app
from tests.conftest import ROOT
from tests.helpers import Book, FakeShop, charts

runner = CliRunner()
CATALOGUE = [Book("u1", "Alpha", 10.0), Book("u2", "Beta Alpha", 20.0), Book("u3", "Gamma", 30.0)]


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    text = (ROOT / "sites.yaml").read_text(encoding="utf-8")
    text = text.replace("delay_seconds: 0.25", "delay_seconds: 0")
    (tmp_path / "sites.yaml").write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def invoke(*args: str, env: dict[str, str] | None = None) -> Result:
    return runner.invoke(app, ["--db", "test.db", *args], env=env)


def test_version_and_help() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and result.stdout.startswith("price-monitor 0.")
    result = runner.invoke(app, ["--help"])
    for command in ("scrape", "watch", "report", "history", "sites", "alerts"):
        assert command in result.stdout


def test_sites_validate_ok(workdir: Path) -> None:
    result = invoke("sites", "validate")
    assert result.exit_code == 0, result.output
    assert "books-toscrape: ok" in result.stdout
    assert "1 site(s), 3 alert rule(s)" in result.stdout


def test_sites_validate_reports_errors(workdir: Path) -> None:
    (workdir / "sites.yaml").write_text(
        "sites:\n  x:\n    start_urls: [https://x.test]\n    currency: USD\n"
        "    item_selector: 'a[['\n",
        encoding="utf-8",
    )
    result = invoke("sites", "validate")
    assert result.exit_code == 2
    assert "invalid configuration" in result.output
    assert "sites.x.item_selector" in result.output


def test_missing_config_file(workdir: Path) -> None:
    result = invoke("--config", "nope.yaml", "sites", "validate")
    assert result.exit_code == 2
    assert "cannot read" in result.output


def test_validate_rejects_broken_plugin(workdir: Path) -> None:
    text = (workdir / "sites.yaml").read_text(encoding="utf-8")
    (workdir / "sites.yaml").write_text(
        text.replace("    key: upc\n", "    key: upc\n    plugin: absent_pkg:Parser\n"),
        encoding="utf-8",
    )
    result = invoke("sites", "validate")
    assert result.exit_code == 2
    assert "cannot import plugin" in result.output


def test_invalid_environment_setting(workdir: Path) -> None:
    result = invoke("sites", "validate", env={"PRICE_MONITOR_LOG_FORMAT": "xml"})
    assert result.exit_code == 2
    assert "invalid settings" in result.output


@respx.mock
def test_scrape_report_history_flow(workdir: Path) -> None:
    shop = FakeShop([list(CATALOGUE)])
    shop.mount(respx)

    first = invoke("scrape")
    assert first.exit_code == 0, first.output
    assert "books-toscrape: 3 products (baseline run" in first.stdout

    shop.pages = [[Book("u1", "Alpha", 7.0), CATALOGUE[1], CATALOGUE[2]]]
    second = invoke("scrape", "--no-alerts")
    assert second.exit_code == 0, second.output
    assert "1 price_change" in second.stdout

    history = invoke("history", "gamma")
    assert history.exit_code == 0, history.output
    assert "Gamma" in history.stdout and "2 observation(s)" in history.stdout

    by_key = invoke("history", "u1", "--limit", "1")
    assert len(by_key.stdout.splitlines()) == 4  # title, header, one observation, summary
    assert "min 7.00, max 10.00 GBP" in by_key.stdout

    out = workdir / "out"
    report = invoke("report", "--out", str(out), "--top", "3")
    assert report.exit_code == 0, report.output
    wb = load_workbook(out / "products_report.xlsx")
    assert len(charts(wb["Top movers"])) == 1
    assert (out / "products.csv").exists()

    only_csv = invoke("report", "--out", str(workdir / "csv"), "--format", "csv")
    assert only_csv.exit_code == 0
    assert not (workdir / "csv" / "products_report.xlsx").exists()


@respx.mock
def test_history_ambiguous_and_unknown(workdir: Path) -> None:
    FakeShop([list(CATALOGUE)]).mount(respx)
    invoke("scrape")
    ambiguous = invoke("history", "alpha")
    assert ambiguous.exit_code == 1
    assert "2 products match" in ambiguous.output and "u2" in ambiguous.output
    assert invoke("history", "zzz").exit_code == 1


def test_report_on_empty_database(workdir: Path) -> None:
    result = invoke("report")
    assert result.exit_code == 1
    assert "no products yet" in result.output


@respx.mock
def test_scrape_failure_sets_exit_code(workdir: Path) -> None:
    FakeShop([list(CATALOGUE)]).mount(respx, robots="User-agent: *\nDisallow: /\n")
    result = invoke("scrape")
    assert result.exit_code == 1
    ignored = invoke("scrape", "--ignore-robots")
    assert ignored.exit_code == 0, ignored.output


def test_scrape_unknown_site(workdir: Path) -> None:
    result = invoke("scrape", "--site", "nope")
    assert result.exit_code == 2
    assert "unknown site" in result.output


@respx.mock
def test_simulate_changes_warns_and_marks_runs(workdir: Path) -> None:
    catalogue = [Book(f"u{i}", f"Book {i}", 10.0 + i) for i in range(30)]
    FakeShop([catalogue]).mount(respx)
    invoke("scrape", "--simulate-changes")
    result = invoke("scrape", "--simulate-changes")
    assert result.exit_code == 0, result.output
    assert "stores altered data" in result.output
    assert "price_change" in result.stdout


@respx.mock
def test_alerts_flow_end_to_end(workdir: Path) -> None:
    shop = FakeShop([list(CATALOGUE)])
    shop.mount(respx)
    hook = respx.post("https://hooks.test/in").respond(200)
    env = {"PRICE_MONITOR_WEBHOOK_URL": "https://hooks.test/in"}
    invoke("scrape", env=env)
    shop.pages = [[Book("u1", "Alpha", 5.0), CATALOGUE[1], CATALOGUE[2]]]
    result = invoke("scrape", env=env)
    assert "alerts matched=1 sent=1" in result.stdout
    body = json.loads(hook.calls.last.request.content)
    assert body["alerts"][0]["rule"] == "price-drop-10"
    again = invoke("scrape", env=env)
    assert "alerts matched" not in again.stdout


def test_alerts_test_command(workdir: Path) -> None:
    assert invoke("alerts", "test").exit_code == 2
    env = {"PRICE_MONITOR_WEBHOOK_URL": "https://hooks.test/in"}
    assert invoke("alerts", "test", "-n", "telegram", env=env).exit_code == 2
    with respx.mock:
        respx.post("https://hooks.test/in").respond(200)
        ok = invoke("alerts", "test", env=env)
    assert ok.exit_code == 0 and "webhook: sent" in ok.stdout
    with respx.mock:
        respx.post("https://hooks.test/in").mock(return_value=httpx.Response(400))
        bad = invoke("alerts", "test", env=env)
    assert bad.exit_code == 1 and "FAILED (HTTP 400)" in bad.output


@respx.mock
def test_watch_runs_job_and_report(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeShop([list(CATALOGUE)]).mount(respx)
    seen: dict[str, float] = {}

    def one_run(
        job: Callable[[], None], *, interval: float, jitter: float, stop: object, rng: object
    ) -> int:
        seen.update(interval=interval, jitter=jitter)
        job()
        return 1

    monkeypatch.setattr(cli, "watch_loop", one_run)
    result = invoke("watch", "--interval", "30m", "--jitter", "0.2", "--report-dir", "rep")
    assert result.exit_code == 0, result.output
    assert seen == {"interval": 1800.0, "jitter": 0.2}
    assert (workdir / "rep" / "products_report.xlsx").exists()


def test_watch_rejects_bad_interval(workdir: Path) -> None:
    result = invoke("watch", "--interval", "soon")
    assert result.exit_code == 2
    assert "invalid interval" in result.output
