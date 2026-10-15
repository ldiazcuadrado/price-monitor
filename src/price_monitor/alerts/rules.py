"""Rule evaluation: which changes become alerts."""

from __future__ import annotations

import re

from price_monitor.config import (
    BackInStockRule,
    BelowTargetRule,
    NewProductRule,
    OutOfStockRule,
    PriceDropRule,
    PriceIncreaseRule,
    RemovedProductRule,
    Rule,
)
from price_monitor.models import Change, ChangeKind


def _price_move(rule: PriceDropRule | PriceIncreaseRule, change: Change, sign: int) -> bool:
    if change.kind is not ChangeKind.PRICE_CHANGE or change.delta is None:
        return False
    if change.delta * sign <= 0:
        return False
    if rule.min_absolute is not None and abs(change.delta) < rule.min_absolute:
        return False
    return rule.min_percent is None or abs(change.percent or 0.0) >= rule.min_percent


def matches(rule: Rule, site: str, change: Change) -> bool:
    if rule.sites is not None and site not in rule.sites:
        return False
    if rule.title_regex and not re.search(rule.title_regex, change.title, re.IGNORECASE):
        return False
    match rule:
        case PriceDropRule():
            return _price_move(rule, change, -1)
        case PriceIncreaseRule():
            return _price_move(rule, change, 1)
        case BelowTargetRule():
            new = change.new_price
            if new is None or new > rule.target:
                return False
            if change.kind is ChangeKind.NEW:
                return True
            return (
                change.kind is ChangeKind.PRICE_CHANGE
                and change.old_price is not None
                and change.old_price > rule.target
            )
        case BackInStockRule():
            return change.kind is ChangeKind.BACK_IN_STOCK
        case OutOfStockRule():
            return change.kind is ChangeKind.OUT_OF_STOCK
        case NewProductRule():
            return change.kind is ChangeKind.NEW
        case RemovedProductRule():
            return change.kind is ChangeKind.REMOVED
    return False  # pragma: no cover
