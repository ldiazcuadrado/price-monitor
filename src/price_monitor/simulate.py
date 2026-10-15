"""Deterministic perturbation of a scrape, to preview change reports on a static site."""

from __future__ import annotations

import random
from dataclasses import replace

from price_monitor.models import Product

GHOST_PREFIX = "simulated-"


def simulate_changes(products: list[Product], seed: int) -> list[Product]:
    """Return a copy of ``products`` with some prices and stock altered.

    A tenth of the prices move, a twentieth go out of stock, a few products vanish and one
    synthetic product appears. Products that were made out of stock by an earlier simulated
    run come back in stock naturally, because the next scrape sees the real data again.
    """
    rng = random.Random(seed)
    data = list(products)
    if len(data) < 6:
        return data
    for index in rng.sample(range(len(data)), k=max(2, len(data) // 10)):
        p = data[index]
        data[index] = replace(p, price=round(p.price * rng.choice([0.8, 0.9, 1.15, 1.3]), 2))
    for index in rng.sample(range(len(data)), k=max(1, len(data) // 20)):
        data[index] = replace(data[index], stock=0, in_stock=False)
    for _ in range(max(1, len(data) // 25)):
        data.pop(rng.randrange(len(data)))
    template = data[0]
    data.append(
        replace(
            template,
            key=f"{GHOST_PREFIX}{seed:03d}",
            title=f"Simulated new product {seed}",
            url=template.url,
            stock=3,
            in_stock=True,
        )
    )
    return data
