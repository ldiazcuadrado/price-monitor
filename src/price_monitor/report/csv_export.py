"""CSV export of the current product list."""

from __future__ import annotations

import csv
from pathlib import Path

from price_monitor.report.data import ReportData

COLUMNS = ["site", "key", "title", "category", "price", "currency", "stock", "in_stock", "url"]


def write_products_csv(data: ReportData, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    attribute_names = sorted({name for p in data.products for name in p.attributes})
    with path.open("w", newline="", encoding="utf-8-sig") as fh:  # BOM so Excel detects UTF-8
        writer = csv.writer(fh)
        writer.writerow([*COLUMNS, *attribute_names])
        for p in data.products:
            writer.writerow(
                [
                    p.site,
                    p.key,
                    p.title,
                    p.category or "",
                    p.price,
                    p.currency,
                    "" if p.stock is None else p.stock,
                    "" if p.in_stock is None else int(p.in_stock),
                    p.url,
                    *[p.attributes.get(name, "") for name in attribute_names],
                ]
            )
    return path
