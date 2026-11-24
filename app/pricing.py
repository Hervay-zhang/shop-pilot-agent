from __future__ import annotations

from dataclasses import dataclass
from itertools import product as cartesian_product
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Coupon, Product, User


@dataclass(frozen=True)
class PriceLine:
    sku: str
    name: str
    quantity: int
    list_price_cents: int
    unit_price_cents: int
    line_total_cents: int


@dataclass(frozen=True)
class PriceQuote:
    lines: list[PriceLine]
    subtotal_cents: int
    coupon_code: str | None
    coupon_name: str | None
    coupon_discount_cents: int
    total_cents: int
    member_saving_cents: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "lines": [
                {
                    "sku": line.sku,
                    "name": line.name,
                    "quantity": line.quantity,
                    "list_price": line.list_price_cents / 100,
                    "unit_price": line.unit_price_cents / 100,
                    "line_total": line.line_total_cents / 100,
                }
                for line in self.lines
            ],
            "subtotal": self.subtotal_cents / 100,
            "member_saving": self.member_saving_cents / 100,
            "coupon_code": self.coupon_code,
            "coupon_name": self.coupon_name,
            "coupon_discount": self.coupon_discount_cents / 100,
            "total": self.total_cents / 100,
            "total_cents": self.total_cents,
            "pricing_engine": "deterministic-v1",
        }


def unit_price_for(product: Product, member: bool) -> int:
    return product.member_price_cents if member else product.price_cents


def best_coupon(
    db: Session,
    user: User,
    subtotal_cents: int,
    requested_code: str | None = None,
) -> Coupon | None:
    if requested_code:
        coupon = db.scalar(select(Coupon).where(Coupon.code == requested_code, Coupon.active))
        if not coupon:
            return None
        if coupon.member_only and not user.member:
            return None
        if subtotal_cents < coupon.min_spend_cents:
            return None
        return coupon

    coupons = db.scalars(select(Coupon).where(Coupon.active)).all()
    eligible = [
        coupon
        for coupon in coupons
        if subtotal_cents >= coupon.min_spend_cents and (user.member or not coupon.member_only)
    ]
    return max(eligible, key=lambda item: item.discount_cents, default=None)


def quote_products(
    db: Session,
    products: list[Product],
    user: User,
    coupon_code: str | None = None,
) -> PriceQuote:
    lines: list[PriceLine] = []
    member_saving = 0
    subtotal = 0
    for item in products:
        unit_price = unit_price_for(item, user.member)
        list_total = item.price_cents
        subtotal += unit_price
        member_saving += list_total - unit_price
        lines.append(
            PriceLine(
                sku=item.sku,
                name=item.name,
                quantity=1,
                list_price_cents=list_total,
                unit_price_cents=unit_price,
                line_total_cents=unit_price,
            )
        )
    coupon = best_coupon(db, user, subtotal, coupon_code)
    discount = min(coupon.discount_cents, subtotal) if coupon else 0
    return PriceQuote(
        lines=lines,
        subtotal_cents=subtotal,
        coupon_code=coupon.code if coupon else None,
        coupon_name=coupon.name if coupon else None,
        coupon_discount_cents=discount,
        total_cents=subtotal - discount,
        member_saving_cents=member_saving,
    )


def choose_bundle(
    db: Session,
    candidates_by_category: dict[str, list[Product]],
    user: User,
    budget_cents: int,
) -> tuple[list[Product], PriceQuote] | None:
    """Evaluate only complete category combinations, preferring relevance then budget fit."""
    if not candidates_by_category or any(not items for items in candidates_by_category.values()):
        return None
    category_order = list(candidates_by_category)
    best: tuple[float, int, list[Product], PriceQuote] | None = None
    # The demo intentionally limits to at most three required categories to keep evaluation bounded.
    for combo in cartesian_product(*(candidates_by_category[cat][:4] for cat in category_order)):
        quote = quote_products(db, list(combo), user)
        if quote.total_cents > budget_cents:
            continue
        relevance = sum(max(0, 4 - index) for index, _ in enumerate(combo))
        # Prefer higher product relevance and a total that uses more of the user's budget.
        fit_score = quote.total_cents / max(1, budget_cents)
        score = float(relevance) + fit_score
        candidate = (score, quote.total_cents, list(combo), quote)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return (best[2], best[3]) if best else None
