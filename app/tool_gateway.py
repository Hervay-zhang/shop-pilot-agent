from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.auth import decode_token
from app.buyer_service import (
    create_service_request,
    derive_idempotency_key,
)
from app.cache import cache
from app.db import SessionLocal
from app.merchant_service import (
    dashboard,
    diagnose,
    propose_change,
    transition_change,
)
from app.models import Coupon, Order, OrderDraft, Product, User
from app.pricing import quote_products, unit_price_for
from app.retrieval import retriever
from app.schemas import (
    ToolActionArgs,
    ToolAfterSalesArgs,
    ToolCartArgs,
    ToolChangeArgs,
    ToolMetricsArgs,
    ToolProductArgs,
    ToolSearchArgs,
)


class EmptyArgs(BaseModel):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    category: str
    scopes: frozenset[str]
    schema: type[BaseModel]
    handler: Callable[[Any, User, BaseModel], dict]
    idempotent: bool = True


def _not_found(code: str = "NOT_FOUND") -> HTTPException:
    return HTTPException(status_code=404, detail={"code": code})


def _search(db, user: User, data: ToolSearchArgs) -> dict:
    categories = (
        [data.category]
        if data.category
        else list({item.category for item in db.scalars(select(Product)).all()})
    )
    grouped = retriever.search(
        db,
        user,
        data.query,
        categories,
        data.max_price_cents or 200000,
    )
    results = [
        {
            "sku": ranked.product.sku,
            "name": ranked.product.name,
            "price": unit_price_for(ranked.product, user.member) / 100,
            "stock": ranked.product.stock,
            "ship_from": ranked.product.ship_from,
            "score": round(ranked.score, 4),
        }
        for values in grouped.values()
        for ranked in values[: data.limit]
    ]
    results.sort(key=lambda item: item["score"], reverse=True)
    return {"items": results[: data.limit], "backend": retriever.vector_status}


def _get_product(db, user: User, data: ToolProductArgs) -> dict:
    product = db.scalar(select(Product).where(Product.sku == data.sku))
    if not product:
        raise _not_found("SKU_NOT_FOUND")
    return {
        "sku": product.sku,
        "name": product.name,
        "category": product.category,
        "price": unit_price_for(product, user.member) / 100,
        "list_price": product.price_cents / 100,
        "stock": product.stock,
        "ship_from": product.ship_from,
        "attributes": product.attributes,
        "description": product.description,
    }


def _inventory_price(db, user: User, data: ToolProductArgs) -> dict:
    product = db.scalar(select(Product).where(Product.sku == data.sku))
    if not product:
        raise _not_found("SKU_NOT_FOUND")
    return {
        "sku": product.sku,
        "stock": product.stock,
        "available": product.active and product.stock > 0,
        "price_cents": unit_price_for(product, user.member),
        "snapshot_at": int(time.time()),
    }


def _list_coupons(db, user: User, data: EmptyArgs) -> dict:
    coupons = db.scalars(select(Coupon).where(Coupon.active)).all()
    return {
        "items": [
            {
                "code": item.code,
                "name": item.name,
                "discount": item.discount_cents / 100,
                "min_spend": item.min_spend_cents / 100,
                "eligible": not item.member_only or user.member,
            }
            for item in coupons
        ]
    }


def _update_cart(db, user: User, data: ToolCartArgs) -> dict:
    product = db.scalar(select(Product).where(Product.sku == data.sku))
    if not product:
        raise _not_found("SKU_NOT_FOUND")
    if product.stock < data.quantity:
        raise HTTPException(status_code=409, detail={"code": "STOCK_NOT_ENOUGH"})
    key = data.idempotency_key or derive_idempotency_key(
        user.id, [data.sku], f"cart-{int(time.time() // 60)}"
    )
    cache_key = f"tool-idempotency:cart.upsert:{user.id}:{key}"
    cached = cache.get_json(cache_key)
    if cached:
        return {**cached, "replayed": True}
    result = {
        "sku": product.sku,
        "quantity": data.quantity,
        "idempotency_key": key,
        "cart_status": "updated",
    }
    cache.set_json(cache_key, result, ttl_seconds=86400)
    return result


def _create_draft(db, user: User, data: ToolCartArgs) -> dict:
    product = db.scalar(select(Product).where(Product.sku == data.sku))
    if not product:
        raise _not_found("SKU_NOT_FOUND")
    key = data.idempotency_key or derive_idempotency_key(
        user.id, [data.sku], f"draft-{int(time.time() // 60)}"
    )
    cache_key = f"tool-idempotency:order.create_draft:{user.id}:{key}"
    cached = cache.get_json(cache_key)
    if cached:
        return {**cached, "replayed": True}
    quote = quote_products(db, [product], user)
    draft = OrderDraft(
        run_id="mcp-direct",
        user_id=user.id,
        payload={
            "items": [{"sku": product.sku, "name": product.name, "quantity": 1}],
            "quote": quote.to_dict(),
        },
        total_cents=quote.total_cents,
    )
    db.add(draft)
    db.commit()
    result = {
        "draft_id": draft.id,
        "total": quote.total_cents / 100,
        "status": draft.status,
        "idempotency_key": key,
    }
    cache.set_json(cache_key, result, ttl_seconds=86400)
    return result


def _get_order(db, user: User, data: ToolProductArgs) -> dict:
    order = db.get(Order, data.sku)
    if not order or (user.role == "buyer" and order.user_id != user.id):
        raise _not_found("ORDER_NOT_FOUND")
    return {
        "order_id": order.id,
        "status": order.status,
        "sku_list": order.sku_list,
        "total": order.total_cents / 100,
    }


def _track_logistics(db, user: User, data: ToolProductArgs) -> dict:
    order = db.get(Order, data.sku)
    if not order or (user.role == "buyer" and order.user_id != user.id):
        raise _not_found("ORDER_NOT_FOUND")
    return {
        "order_id": order.id,
        "status": "in_transit",
        "carrier": "顺丰速运",
        "latest_event": "快件已到达上海浦东转运中心",
        "events": [
            {"time": "09:12", "text": "快件已揽收"},
            {"time": "16:35", "text": "快件已到达上海浦东转运中心"},
        ],
    }


def _list_policies(db, user: User, data: ToolProductArgs) -> dict:
    category = data.sku
    policy = (
        "支持签收后 7 天无理由退货；电脑包需保持吊牌完整。"
        if category == "电脑包"
        else "支持签收后 7 天无理由退货；已拆封个护商品仅质量问题可退。"
    )
    return {
        "category": category,
        "policies": [{"title": "退换政策", "content": policy, "source": "after-sale-policy-v1"}],
    }


def _create_after_sales(db, user: User, data: ToolAfterSalesArgs) -> dict:
    item = create_service_request(
        db,
        user,
        "refund",
        data.order_id,
        {"reason": data.reason},
    )
    return {
        "request_id": item.id,
        "status": item.status,
        "requires_confirmation": True,
        "requires_human_review": True,
    }


def _metrics(db, user: User, data: ToolMetricsArgs) -> dict:
    return dashboard(db, data.days)


def _diagnose(db, user: User, data: EmptyArgs) -> dict:
    return diagnose(db)


def _propose_change(db, user: User, data: ToolActionArgs) -> dict:
    change = propose_change(
        db,
        user,
        data.kind,
        data.sku,
        data.new_price_cents,
        data.campaign_budget_cents,
    )
    return {
        "change_order_id": change.id,
        "status": change.status,
        "approval_required": change.approval_required,
        "expected_gmv_delta": change.expected_gmv_delta_cents / 100,
        "impact_scope": change.impact_scope,
    }


def _transition_change(db, user: User, data: ToolChangeArgs) -> dict:
    change = transition_change(db, user, data.change_order_id, data.action)
    return {"change_order_id": change.id, "status": change.status}


TOOL_SPECS = [
    ToolSpec(
        "catalog.search",
        "按语义、类目、预算和库存检索商品",
        "商品",
        frozenset({"buyer", "merchant"}),
        ToolSearchArgs,
        _search,
    ),
    ToolSpec(
        "catalog.get_product",
        "读取 SPU 详情与结构化属性",
        "商品",
        frozenset({"buyer", "merchant"}),
        ToolProductArgs,
        _get_product,
    ),
    ToolSpec(
        "inventory.get_price",
        "读取实时库存与会员价格",
        "库存价格",
        frozenset({"buyer", "merchant"}),
        ToolProductArgs,
        _inventory_price,
    ),
    ToolSpec(
        "coupon.list",
        "获取可用于当前用户的优惠券",
        "优惠券",
        frozenset({"buyer"}),
        EmptyArgs,
        _list_coupons,
    ),
    ToolSpec(
        "cart.upsert",
        "带幂等键更新购物车",
        "购物车",
        frozenset({"buyer"}),
        ToolCartArgs,
        _update_cart,
    ),
    ToolSpec(
        "order.create_draft",
        "创建待确认订单草稿",
        "订单",
        frozenset({"buyer"}),
        ToolCartArgs,
        _create_draft,
    ),
    ToolSpec(
        "order.get",
        "查询订单详情",
        "订单",
        frozenset({"buyer", "merchant"}),
        ToolProductArgs,
        _get_order,
    ),
    ToolSpec(
        "logistics.track",
        "查询物流轨迹",
        "物流",
        frozenset({"buyer", "merchant"}),
        ToolProductArgs,
        _track_logistics,
    ),
    ToolSpec(
        "after_sales.list_policies",
        "按商品类目读取售后政策",
        "售后",
        frozenset({"buyer", "merchant"}),
        ToolProductArgs,
        _list_policies,
    ),
    ToolSpec(
        "after_sales.create_request",
        "创建需二次确认与人工复核的售后单",
        "售后",
        frozenset({"buyer"}),
        ToolAfterSalesArgs,
        _create_after_sales,
        idempotent=False,
    ),
    ToolSpec(
        "merchant.metrics",
        "通过指标语义层查询经营指标",
        "经营指标",
        frozenset({"merchant"}),
        ToolMetricsArgs,
        _metrics,
    ),
    ToolSpec(
        "merchant.diagnose",
        "沿曝光到退款漏斗输出可解释归因",
        "经营指标",
        frozenset({"merchant"}),
        EmptyArgs,
        _diagnose,
    ),
    ToolSpec(
        "merchant.propose_change",
        "创建带影响范围和回滚参数的变更单",
        "经营动作",
        frozenset({"merchant"}),
        ToolActionArgs,
        _propose_change,
        idempotent=False,
    ),
    ToolSpec(
        "merchant.change_transition",
        "审批、执行或回滚经营动作",
        "经营动作",
        frozenset({"merchant"}),
        ToolChangeArgs,
        _transition_change,
        idempotent=False,
    ),
]
TOOLS = {spec.name: spec for spec in TOOL_SPECS}


class GatewayError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


class ToolGateway:
    """Unified auth, validation, timeout, retry and error mapping for MCP tools."""

    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")

    def catalog(self) -> list[dict]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "category": spec.category,
                "scopes": sorted(spec.scopes),
                "idempotent": spec.idempotent,
                "input_schema": spec.schema.model_json_schema(),
            }
            for spec in TOOL_SPECS
        ]

    def invoke(self, name: str, token: str, arguments: dict) -> dict:
        claims = decode_token(token)
        return self.invoke_as_user(name, claims["sub"], arguments)

    def invoke_as_user(self, name: str, user_id: str, arguments: dict) -> dict:
        started = time.perf_counter()
        spec = TOOLS.get(name)
        if not spec:
            return self._error("TOOL_NOT_FOUND", "未知工具", started)
        try:
            payload = spec.schema.model_validate(arguments)
        except ValidationError as exc:
            return self._error(
                "VALIDATION_ERROR",
                f"参数校验失败：{exc.errors()[0].get('msg', 'invalid input')}",
                started,
            )

        last_error: Exception | None = None
        for attempt in range(1, 3):
            future = self.executor.submit(self._execute, spec, user_id, payload)
            try:
                data = future.result(timeout=2.5)
                return {
                    "ok": True,
                    "code": "OK",
                    "data": data,
                    "meta": {
                        "tool": name,
                        "attempt": attempt,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    },
                }
            except FutureTimeout:
                future.cancel()
                last_error = GatewayError("UPSTREAM_TIMEOUT", "工具执行超时", retryable=True)
            except HTTPException as exc:
                detail = (
                    exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                )
                code = detail.get("code", "TOOL_ERROR")
                retryable = exc.status_code >= 500
                if retryable and attempt == 1:
                    last_error = exc
                    time.sleep(0.12)
                    continue
                return self._error(code, detail.get("message", code), started, attempt)
            except Exception as exc:
                last_error = exc
                if attempt == 1:
                    time.sleep(0.12)
                    continue
        code = getattr(last_error, "code", "INTERNAL_ERROR")
        message = getattr(last_error, "message", "工具执行失败")
        return self._error(code, message, started, 2)

    @staticmethod
    def _execute(spec: ToolSpec, user_id: str, payload: BaseModel) -> dict:
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if not user:
                raise HTTPException(status_code=401, detail={"code": "USER_NOT_FOUND"})
            if user.role not in spec.scopes:
                raise HTTPException(status_code=403, detail={"code": "TOOL_SCOPE_FORBIDDEN"})
            return spec.handler(db, user, payload)

    @staticmethod
    def _error(code: str, message: str, started: float, attempt: int = 1) -> dict:
        return {
            "ok": False,
            "code": code,
            "message": message,
            "meta": {
                "attempt": attempt,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        }


gateway = ToolGateway()
