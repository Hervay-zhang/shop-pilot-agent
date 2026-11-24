from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from app.tool_gateway import gateway

mcp = FastMCP(
    name="ShopPilot Commerce Tools",
    instructions="电商买卖双方共用的 14 个受控业务工具。所有工具都需要演示 JWT。",
)


def _call(token: str, name: str, **arguments: Any) -> dict:
    return gateway.invoke(name, token, arguments)


@mcp.tool
def catalog_search(token: str, query: str, category: str | None = None, limit: int = 8) -> dict:
    """按语义、类目、预算和库存检索商品。"""
    return _call(token, "catalog.search", query=query, category=category, limit=limit)


@mcp.tool
def catalog_get_product(token: str, sku: str) -> dict:
    """读取 SPU 详情与结构化属性。"""
    return _call(token, "catalog.get_product", sku=sku)


@mcp.tool
def inventory_get_price(token: str, sku: str) -> dict:
    """读取实时库存与会员价格。"""
    return _call(token, "inventory.get_price", sku=sku)


@mcp.tool
def coupon_list(token: str) -> dict:
    """获取当前用户可用的优惠券。"""
    return _call(token, "coupon.list")


@mcp.tool
def cart_upsert(
    token: str, sku: str, quantity: int = 1, idempotency_key: str | None = None
) -> dict:
    """带幂等键更新购物车。"""
    return _call(
        token,
        "cart.upsert",
        sku=sku,
        quantity=quantity,
        idempotency_key=idempotency_key,
    )


@mcp.tool
def order_create_draft(token: str, sku: str, quantity: int = 1) -> dict:
    """创建待确认订单草稿。"""
    return _call(token, "order.create_draft", sku=sku, quantity=quantity)


@mcp.tool
def order_get(token: str, order_id: str) -> dict:
    """查询订单详情。"""
    return _call(token, "order.get", sku=order_id)


@mcp.tool
def logistics_track(token: str, order_id: str) -> dict:
    """查询物流轨迹。"""
    return _call(token, "logistics.track", sku=order_id)


@mcp.tool
def after_sales_list_policies(token: str, category: str) -> dict:
    """按商品类目读取售后政策。"""
    return _call(token, "after_sales.list_policies", sku=category)


@mcp.tool
def after_sales_create_request(token: str, order_id: str, reason: str) -> dict:
    """创建需二次确认与人工复核的售后单。"""
    return _call(token, "after_sales.create_request", order_id=order_id, reason=reason)


@mcp.tool
def merchant_metrics(token: str, days: int = 7) -> dict:
    """通过指标语义层查询经营指标。"""
    return _call(token, "merchant.metrics", days=days)


@mcp.tool
def merchant_diagnose(token: str) -> dict:
    """沿曝光到退款漏斗输出可解释归因。"""
    return _call(token, "merchant.diagnose")


@mcp.tool
def merchant_propose_change(
    token: str,
    kind: str,
    sku: str,
    new_price_cents: int | None = None,
    campaign_budget_cents: int | None = None,
) -> dict:
    """创建带影响范围和回滚参数的变更单。"""
    return _call(
        token,
        "merchant.propose_change",
        kind=kind,
        sku=sku,
        new_price_cents=new_price_cents,
        campaign_budget_cents=campaign_budget_cents,
    )


@mcp.tool
def merchant_change_transition(token: str, change_order_id: str, action: str) -> dict:
    """审批、执行或回滚经营动作。"""
    return _call(
        token, "merchant.change_transition", change_order_id=change_order_id, action=action
    )


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001)
