from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    AuditLog,
    BehaviorEvent,
    ChangeOrder,
    ExperimentResult,
    Product,
    User,
)

FUNNEL_STAGES = ("impression", "click", "add_cart", "pay", "refund")
STAGE_LABELS = {
    "impression": "曝光",
    "click": "点击",
    "add_cart": "加购",
    "pay": "支付",
    "refund": "退款",
}

METRIC_DEFINITIONS = [
    {
        "name": "GMV",
        "formula": "SUM(pay.amount_cents)",
        "grain": ["day", "week"],
        "source": "交易库",
        "note": "去退款前口径",
    },
    {
        "name": "UV",
        "formula": "SUM(behavior.impression)",
        "grain": ["day", "week"],
        "source": "买家行为回流",
        "note": "演示环境以曝光去重近似",
    },
    {
        "name": "支付转化率",
        "formula": "SUM(pay.count) / SUM(impression.count)",
        "grain": ["day", "week"],
        "source": "交易库 + 行为回流",
        "note": "全链路支付转化",
    },
    {
        "name": "退款率",
        "formula": "SUM(refund.count) / SUM(pay.count)",
        "grain": ["day", "week"],
        "source": "售后库",
        "note": "按支付订单口径",
    },
    {
        "name": "加购率",
        "formula": "SUM(add_cart.count) / SUM(click.count)",
        "grain": ["day"],
        "source": "买家行为回流",
        "note": "用于诊断意向断层",
    },
    {
        "name": "客单价",
        "formula": "GMV / SUM(pay.count)",
        "grain": ["day", "week"],
        "source": "交易库",
        "note": "支付订单均值",
    },
]


def safe_rate(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _stage_totals(events: list[BehaviorEvent]) -> dict[str, int]:
    values = defaultdict(int)
    for event in events:
        values[event.stage] += event.count
    return dict(values)


def _gmv(events: list[BehaviorEvent]) -> int:
    return sum(event.amount_cents for event in events if event.stage == "pay")


def dashboard(db: Session, days: int = 7) -> dict[str, Any]:
    today = date.today()
    current_start = today - timedelta(days=days)
    previous_start = current_start - timedelta(days=days)
    current_events = db.scalars(
        select(BehaviorEvent).where(
            BehaviorEvent.event_date >= current_start, BehaviorEvent.event_date < today
        )
    ).all()
    previous_events = db.scalars(
        select(BehaviorEvent).where(
            BehaviorEvent.event_date >= previous_start, BehaviorEvent.event_date < current_start
        )
    ).all()
    current_stage = _stage_totals(current_events)
    previous_stage = _stage_totals(previous_events)
    current_gmv = _gmv(current_events)
    previous_gmv = _gmv(previous_events)
    current_cvr = safe_rate(current_stage.get("pay", 0), current_stage.get("impression", 0))
    previous_cvr = safe_rate(previous_stage.get("pay", 0), previous_stage.get("impression", 0))
    current_refund = safe_rate(current_stage.get("refund", 0), current_stage.get("pay", 0))
    previous_refund = safe_rate(previous_stage.get("refund", 0), previous_stage.get("pay", 0))

    return {
        "window": {
            "days": days,
            "current": [current_start.isoformat(), (today - timedelta(days=1)).isoformat()],
            "previous": [
                previous_start.isoformat(),
                (current_start - timedelta(days=1)).isoformat(),
            ],
        },
        "cards": [
            {
                "key": "gmv",
                "label": "GMV",
                "value": current_gmv / 100,
                "format": "currency",
                "delta": round(safe_rate(current_gmv - previous_gmv, previous_gmv) * 100, 1),
            },
            {
                "key": "uv",
                "label": "UV",
                "value": current_stage.get("impression", 0),
                "format": "integer",
                "delta": round(
                    safe_rate(
                        current_stage.get("impression", 0) - previous_stage.get("impression", 0),
                        previous_stage.get("impression", 0),
                    )
                    * 100,
                    1,
                ),
            },
            {
                "key": "cvr",
                "label": "支付转化率",
                "value": round(current_cvr * 100, 2),
                "format": "percent",
                "delta": round((current_cvr - previous_cvr) * 100, 2),
            },
            {
                "key": "refund",
                "label": "退款率",
                "value": round(current_refund * 100, 2),
                "format": "percent",
                "delta": round((current_refund - previous_refund) * 100, 2),
            },
        ],
        "funnel": [
            {"stage": stage, "label": STAGE_LABELS[stage], "value": current_stage.get(stage, 0)}
            for stage in FUNNEL_STAGES
        ],
        "metric_definitions": METRIC_DEFINITIONS,
        "semantic_layer_version": "commerce-metrics-v1",
        "sources": ["交易库", "ERP", "广告后台", "买家行为回流"],
    }


def diagnose(db: Session) -> dict[str, Any]:
    today = date.today()
    current_start = today - timedelta(days=2)
    previous_start = today - timedelta(days=5)
    current = db.scalars(
        select(BehaviorEvent).where(
            BehaviorEvent.event_date >= current_start, BehaviorEvent.event_date < today
        )
    ).all()
    previous = db.scalars(
        select(BehaviorEvent).where(
            BehaviorEvent.event_date >= previous_start,
            BehaviorEvent.event_date < current_start,
        )
    ).all()

    grouped_current: dict[tuple[str, str], list[BehaviorEvent]] = defaultdict(list)
    grouped_previous: dict[tuple[str, str], list[BehaviorEvent]] = defaultdict(list)
    for event in current:
        grouped_current[(event.sku, event.stage)].append(event)
    for event in previous:
        grouped_previous[(event.sku, event.stage)].append(event)

    anomalies: list[dict[str, Any]] = []
    for key, events in grouped_current.items():
        sku, stage = key
        current_value = sum(event.count for event in events) / 2
        previous_value = sum(event.count for event in grouped_previous.get(key, [])) / 3
        if previous_value == 0:
            continue
        delta_pct = (current_value - previous_value) / previous_value * 100
        if abs(delta_pct) >= 8:
            anomalies.append(
                {
                    "sku": sku,
                    "stage": stage,
                    "stage_label": STAGE_LABELS[stage],
                    "current_daily_avg": round(current_value, 1),
                    "previous_daily_avg": round(previous_value, 1),
                    "delta_pct": round(delta_pct, 1),
                    "absolute_delta": round(current_value - previous_value, 1),
                }
            )
    anomalies.sort(key=lambda item: abs(item["delta_pct"]), reverse=True)
    primary = anomalies[0] if anomalies else None
    if primary:
        evidence_events = db.scalars(
            select(BehaviorEvent).where(
                BehaviorEvent.sku == primary["sku"],
                BehaviorEvent.event_date >= previous_start,
                BehaviorEvent.event_date < today,
            )
        ).all()
        sample_size = sum(item.count for item in evidence_events)
        if sample_size >= 1500:
            strength = "high"
        elif sample_size >= 500:
            strength = "medium"
        else:
            strength = "low"
        primary["evidence_strength"] = strength
        primary["sample_size"] = sample_size

    products = {
        product.sku: product for product in db.scalars(select(Product).where(Product.active)).all()
    }
    for item in anomalies[:6]:
        product = products.get(item["sku"])
        item["product_name"] = product.name if product else item["sku"]

    evidence = []
    if primary:
        add_cart = next(
            (
                item
                for item in anomalies
                if item["sku"] == primary["sku"] and item["stage"] == "add_cart"
            ),
            None,
        )
        pay = next(
            (
                item
                for item in anomalies
                if item["sku"] == primary["sku"] and item["stage"] == "pay"
            ),
            None,
        )
        evidence = [
            {
                "source": "买家行为回流",
                "label": "加购变化",
                "value": f"{add_cart['delta_pct']:+.1f}%" if add_cart else "样本不足",
            },
            {
                "source": "交易库",
                "label": "支付变化",
                "value": f"{pay['delta_pct']:+.1f}%" if pay else "样本不足",
            },
            {
                "source": "售后库",
                "label": "退款变化",
                "value": "未出现显著异常",
            },
        ]

    return {
        "status": "diagnosed" if primary else "insufficient_evidence",
        "summary": (
            f"{primary['product_name']} 的{primary['stage_label']}环节下降 "
            f"{abs(primary['delta_pct']):.1f}%，是当前最显著异动。"
            if primary
            else "当前数据不足以形成高置信度归因，请补充更长时间窗或流量数据。"
        ),
        "primary": primary,
        "anomalies": anomalies[:6],
        "evidence": evidence,
        "guardrail": "模型不直接计算指标；数值均由指标语义层输出并附带窗口和样本量。",
        "window": {
            "current": [current_start.isoformat(), (today - timedelta(days=1)).isoformat()],
            "baseline": [
                previous_start.isoformat(),
                (current_start - timedelta(days=1)).isoformat(),
            ],
        },
    }


def _daily_paid(db: Session, sku: str, days: int = 7) -> tuple[float, int]:
    start = date.today() - timedelta(days=days)
    events = db.scalars(
        select(BehaviorEvent).where(
            BehaviorEvent.sku == sku,
            BehaviorEvent.stage == "pay",
            BehaviorEvent.event_date >= start,
        )
    ).all()
    return sum(item.count for item in events) / days, sum(item.amount_cents for item in events)


def propose_change(
    db: Session,
    user: User,
    kind: str,
    sku: str,
    new_price_cents: int | None,
    campaign_budget_cents: int | None,
) -> ChangeOrder:
    product = db.scalar(select(Product).where(Product.sku == sku))
    if not product:
        raise HTTPException(status_code=404, detail={"code": "SKU_NOT_FOUND"})
    daily_orders, weekly_gmv = _daily_paid(db, sku)
    risk_amount = 0
    expected_delta = 0
    payload: dict[str, Any] = {}

    if kind == "price_change":
        if not new_price_cents:
            raise HTTPException(status_code=422, detail={"code": "PRICE_REQUIRED"})
        if new_price_cents > product.price_cents * 2:
            raise HTTPException(status_code=422, detail={"code": "PRICE_OUT_OF_BOUNDS"})
        change_ratio = (new_price_cents - product.price_cents) / product.price_cents
        demand_multiplier = 1 - change_ratio * 1.6
        new_daily_gmv = daily_orders * demand_multiplier * new_price_cents
        old_daily_gmv = weekly_gmv / 7
        expected_delta = round(new_daily_gmv - old_daily_gmv)
        risk_amount = product.price_cents * max(product.stock, 1)
        payload = {
            "new_price_cents": new_price_cents,
            "old_price_cents": product.price_cents,
            "effective_window": "next 7 days",
        }
    elif kind == "campaign":
        if not campaign_budget_cents:
            raise HTTPException(status_code=422, detail={"code": "BUDGET_REQUIRED"})
        risk_amount = campaign_budget_cents
        expected_delta = round(campaign_budget_cents * 1.35)
        payload = {"campaign_budget_cents": campaign_budget_cents}
    else:
        if not campaign_budget_cents:
            raise HTTPException(status_code=422, detail={"code": "BUDGET_REQUIRED"})
        risk_amount = campaign_budget_cents
        expected_delta = 0
        payload = {"new_budget_cents": campaign_budget_cents}

    approval_required = risk_amount > settings.approval_threshold_cents
    change = ChangeOrder(
        kind=kind,
        sku=sku,
        payload=payload,
        before_snapshot={
            "price_cents": product.price_cents,
            "member_price_cents": product.member_price_cents,
            "stock": product.stock,
        },
        expected_gmv_delta_cents=expected_delta,
        impact_scope={
            "sku_count": 1,
            "stock_at_risk": product.stock,
            "risk_amount_cents": risk_amount,
            "window_days": 7,
            "baseline_daily_orders": round(daily_orders, 2),
        },
        status="pending_approval" if approval_required else "approved",
        approval_required=approval_required,
        created_by=user.id,
    )
    db.add(change)
    db.flush()
    add_audit(db, user.id, "propose_change", change.id, {}, change.payload)
    db.commit()
    return change


def transition_change(db: Session, user: User, change_id: str, action: str) -> ChangeOrder:
    change = db.get(ChangeOrder, change_id)
    if not change:
        raise HTTPException(status_code=404, detail={"code": "CHANGE_NOT_FOUND"})
    product = db.scalar(select(Product).where(Product.sku == change.sku))
    if not product:
        raise HTTPException(status_code=404, detail={"code": "SKU_NOT_FOUND"})
    before = {
        "price_cents": product.price_cents,
        "member_price_cents": product.member_price_cents,
        "stock": product.stock,
    }

    if action == "approve":
        if change.status != "pending_approval":
            raise HTTPException(status_code=409, detail={"code": "INVALID_CHANGE_STATE"})
        change.status = "approved"
    elif action == "execute":
        if change.status not in {"approved", "pending_approval"}:
            raise HTTPException(status_code=409, detail={"code": "INVALID_CHANGE_STATE"})
        if change.approval_required and change.status == "pending_approval":
            raise HTTPException(
                status_code=409,
                detail={"code": "APPROVAL_REQUIRED", "message": "高风险变更需先审批"},
            )
        if change.kind == "price_change":
            new_price = int(change.payload["new_price_cents"])
            product.price_cents = new_price
            product.member_price_cents = round(new_price * 0.94)
        change.status = "executed"
        change.executed_at = datetime.now()
    elif action == "rollback":
        if change.status != "executed":
            raise HTTPException(status_code=409, detail={"code": "INVALID_CHANGE_STATE"})
        snapshot = change.before_snapshot
        product.price_cents = int(snapshot["price_cents"])
        product.member_price_cents = int(snapshot["member_price_cents"])
        product.stock = int(snapshot.get("stock", product.stock))
        change.status = "rolled_back"
        change.rolled_back_at = datetime.now()
    else:
        raise HTTPException(status_code=422, detail={"code": "UNKNOWN_ACTION"})

    after = {
        "price_cents": product.price_cents,
        "member_price_cents": product.member_price_cents,
        "stock": product.stock,
    }
    add_audit(db, user.id, action, change.id, before, after)
    db.commit()
    return change


def add_audit(
    db: Session,
    actor_id: str,
    action: str,
    target: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target=target,
            before=before,
            after=after,
        )
    )


def list_changes(db: Session) -> list[dict[str, Any]]:
    changes = db.scalars(select(ChangeOrder).order_by(ChangeOrder.created_at.desc())).all()
    return [
        {
            "id": item.id,
            "kind": item.kind,
            "sku": item.sku,
            "payload": item.payload,
            "before_snapshot": item.before_snapshot,
            "expected_gmv_delta": item.expected_gmv_delta_cents / 100,
            "impact_scope": {
                **item.impact_scope,
                "risk_amount": item.impact_scope.get("risk_amount_cents", 0) / 100,
            },
            "status": item.status,
            "approval_required": item.approval_required,
            "created_at": item.created_at.isoformat(),
            "executed_at": item.executed_at.isoformat() if item.executed_at else None,
            "rolled_back_at": item.rolled_back_at.isoformat() if item.rolled_back_at else None,
        }
        for item in changes
    ]


def experiment_report(db: Session) -> dict[str, Any]:
    experiments = db.scalars(
        select(ExperimentResult).order_by(ExperimentResult.created_at.desc())
    ).all()
    reports = []
    for item in experiments:
        control_rate = safe_rate(item.control_conversions, item.control_visitors)
        treatment_rate = safe_rate(item.treatment_conversions, item.treatment_visitors)
        pooled = safe_rate(
            item.control_conversions + item.treatment_conversions,
            item.control_visitors + item.treatment_visitors,
        )
        std_error = math.sqrt(
            pooled
            * (1 - pooled)
            * (1 / max(1, item.control_visitors) + 1 / max(1, item.treatment_visitors))
        )
        z_score = (treatment_rate - control_rate) / std_error if std_error else 0
        probability = 0.5 * (1 + math.erf(abs(z_score) / math.sqrt(2)))
        reports.append(
            {
                "id": item.id,
                "name": item.name,
                "control": {
                    "visitors": item.control_visitors,
                    "conversions": item.control_conversions,
                    "conversion_rate": round(control_rate * 100, 2),
                },
                "treatment": {
                    "visitors": item.treatment_visitors,
                    "conversions": item.treatment_conversions,
                    "conversion_rate": round(treatment_rate * 100, 2),
                },
                "relative_lift": round(
                    safe_rate(treatment_rate - control_rate, control_rate) * 100, 1
                ),
                "confidence": round(probability * 100, 1),
                "refund_rate_delta": round(item.refund_rate_delta * 100, 2),
                "decision": "continue" if probability >= 0.95 else "collect_more_data",
            }
        )
    return {
        "experiments": reports,
        "note": "效果回测将变更单、实验分组和退款变化配对；百分比置信度仅用于演示。",
    }


def daily_report(db: Session) -> dict[str, Any]:
    board = dashboard(db, 7)
    diff = diagnose(db)
    gmv = next(card for card in board["cards"] if card["key"] == "gmv")
    return {
        "title": f"{date.today().isoformat()} 店铺经营日报",
        "headline": f"近 7 日 GMV ¥{gmv['value']:,.2f}，环比 {gmv['delta']:+.1f}%",
        "diagnosis": diff["summary"],
        "evidence_strength": diff["primary"]["evidence_strength"] if diff["primary"] else "low",
        "next_action": "先复核灰色通勤包流量下滑来源，再决定是否执行价格实验。",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
