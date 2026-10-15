from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from price_monitor.config import ConfigError, load_config, parse_config
from tests.conftest import ROOT


def minimal_site(**overrides: Any) -> dict[str, Any]:
    site: dict[str, Any] = {
        "start_urls": ["https://shop.test/list"],
        "currency": "eur",
        "item_selector": "li.item",
        "fields": {
            "title": {"css": "a"},
            "price": {"css": ".price", "transforms": [{"type": "price", "locale": "de_DE"}]},
            "url": {"css": "a", "attr": "href"},
        },
    }
    site.update(overrides)
    return site


def config_with(**site_overrides: Any) -> dict[str, Any]:
    return {"sites": {"shop": minimal_site(**site_overrides)}}


@pytest.mark.parametrize("name", ["sites.yaml", "examples/sites.example.yaml"])
def test_shipped_configs_are_valid(name: str) -> None:
    config = load_config(ROOT / name)
    assert config.sites
    assert all(site.name for site in config.sites.values())


def test_defaults_and_normalisation() -> None:
    site = parse_config(config_with()).sites["shop"]
    assert site.name == "shop"
    assert site.currency == "EUR"
    assert site.key == "url"
    assert site.request.concurrency == 1
    assert site.renderer == "http"


def test_browser_renderer_forces_single_worker() -> None:
    site = parse_config(config_with(renderer="browser", request={"concurrency": 4})).sites["shop"]
    assert site.request.concurrency == 1


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"start_urls": []}, "start_urls"),
        ({"start_urls": ["ftp://x"]}, "not an http"),
        ({"currency": "euro"}, "currency"),
        ({"item_selector": None}, "item_selector is required"),
        ({"item_selector": "a[["}, "invalid CSS selector"),
        ({"fields": {"title": {"css": "a"}}}, "fields must define: price"),
        ({"key": "sku"}, "key field 'sku'"),
        (
            {
                "detail": {"fields": {"x": {"css": "p"}}},
                "fields": {"title": {"css": "a"}, "price": {"css": "b"}},
            },
            "require a 'url' field",
        ),
        ({"plugin": "not a ref"}, "package.module:attribute"),
        ({"unexpected": 1}, "Extra inputs"),
        ({"pagination": {"type": "url_template", "template": "https://x/{id}"}}, "{page}"),
        ({"request": {"concurrency": 99}}, "concurrency"),
        (
            {"fields": {"title": {"css": "a"}, "price": {"transforms": [{"type": "nope"}]}}},
            "price",
        ),
    ],
)
def test_invalid_site(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(config_with(**overrides))


def test_plugin_site_needs_no_selectors() -> None:
    site = parse_config(config_with(plugin="pkg.mod:Parser", item_selector=None, fields={}))
    assert site.sites["shop"].plugin == "pkg.mod:Parser"


def test_alert_rules_validation() -> None:
    data = config_with()
    data["alerts"] = {
        "rules": [
            {"name": "a", "type": "price_drop", "min_percent": 10},
            {"name": "b", "type": "below_target", "target": 20, "sites": ["shop"]},
        ]
    }
    assert len(parse_config(data).alerts.rules) == 2

    data["alerts"]["rules"].append({"name": "a", "type": "back_in_stock"})
    with pytest.raises(ConfigError, match="duplicate rule names"):
        parse_config(data)

    data["alerts"]["rules"] = [{"name": "x", "type": "back_in_stock", "sites": ["ghost"]}]
    with pytest.raises(ConfigError, match="unknown sites"):
        parse_config(data)

    data["alerts"]["rules"] = [{"name": "x", "type": "price_drop", "title_regex": "("}]
    with pytest.raises(ConfigError, match="invalid regex"):
        parse_config(data)

    data["alerts"]["rules"] = [{"name": "x", "type": "below_target"}]
    with pytest.raises(ConfigError, match="target"):
        parse_config(data)


def test_selected_sites() -> None:
    data = config_with()
    data["sites"]["off"] = minimal_site(enabled=False)
    config = parse_config(data)
    assert [s.name for s in config.selected()] == ["shop"]
    assert [s.name for s in config.selected(["off"])] == ["off"]
    with pytest.raises(ConfigError, match="unknown site"):
        config.selected(["nope"])


def test_load_config_file_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("sites: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(bad)
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(bad)
    bad.write_text("sites: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid configuration"):
        load_config(bad)
