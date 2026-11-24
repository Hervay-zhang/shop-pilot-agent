from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine
from app.models import (
    AuditLog,
    BehaviorEvent,
    ChangeOrder,
    Coupon,
    ExperimentResult,
    Product,
    User,
)

PRODUCTS = [
    {
        "sku": "BAG-16-BLK",
        "name": "城市通勤 16 寸防泼水双肩包",
        "category": "电脑包",
        "price_cents": 36900,
        "member_price_cents": 34900,
        "stock": 83,
        "ship_from": "上海",
        "description": "独立 16 寸电脑仓，轻量背负，适合通勤与短途差旅。",
        "rich_text": "材质：高密度防泼水尼龙；电脑仓：可放 16 寸笔记本；重量：760g；颜色：曜石黑",
        "attributes": {
            "laptop_size": 16,
            "scene": ["通勤", "差旅"],
            "water_resistant": True,
            "weight_g": 760,
            "color": "曜石黑",
        },
        "tags": ["16寸电脑包", "通勤包", "防泼水", "双肩包", "上海发货"],
    },
    {
        "sku": "BAG-16-GRY",
        "name": "城市通勤 16 寸轻量双肩包",
        "category": "电脑包",
        "price_cents": 31900,
        "member_price_cents": 29900,
        "stock": 41,
        "ship_from": "杭州",
        "description": "轻量通勤款，可放 16 寸电脑，背幅透气。",
        "rich_text": "材质：耐磨聚酯纤维；电脑仓：16 寸；重量：690g；颜色：雾灰",
        "attributes": {
            "laptop_size": 16,
            "scene": ["通勤"],
            "water_resistant": False,
            "weight_g": 690,
            "color": "雾灰",
        },
        "tags": ["16寸电脑包", "通勤包", "轻量", "高性价比"],
    },
    {
        "sku": "BAG-14-CRM",
        "name": "简约 14 寸电脑手提包",
        "category": "电脑包",
        "price_cents": 23900,
        "member_price_cents": 21900,
        "stock": 120,
        "ship_from": "上海",
        "description": "轻薄手提电脑包，适合日常办公。",
        "rich_text": "材质：环保皮革；电脑仓：14 寸；重量：520g；颜色：奶油白",
        "attributes": {
            "laptop_size": 14,
            "scene": ["办公"],
            "water_resistant": False,
            "weight_g": 520,
            "color": "奶油白",
        },
        "tags": ["14寸电脑包", "手提包", "办公"],
    },
    {
        "sku": "SUN-50-MLD",
        "name": "轻透防晒乳 SPF50+",
        "category": "防晒",
        "price_cents": 12900,
        "member_price_cents": 11900,
        "stock": 260,
        "ship_from": "上海",
        "description": "清爽不粘腻，通勤成膜快，适合日常户外。",
        "rich_text": "防晒指数：SPF50+ PA++++；规格：50ml；肤感：清爽；场景：通勤/户外",
        "attributes": {"spf": 50, "volume_ml": 50, "finish": "清爽", "scene": ["通勤", "户外"]},
        "tags": ["防晒霜", "通勤", "清爽", "SPF50"],
    },
    {
        "sku": "SUN-30-WATER",
        "name": "水感防晒乳 SPF30",
        "category": "防晒",
        "price_cents": 9900,
        "member_price_cents": 8900,
        "stock": 340,
        "ship_from": "广州",
        "description": "水感轻薄，适合日常补涂。",
        "rich_text": "防晒指数：SPF30 PA+++；规格：40ml；肤感：水润；场景：室内/短时外出",
        "attributes": {"spf": 30, "volume_ml": 40, "finish": "水润", "scene": ["日常"]},
        "tags": ["防晒", "水感", "轻薄"],
    },
    {
        "sku": "UMB-AUTO-BLU",
        "name": "自动折叠晴雨伞",
        "category": "晴雨伞",
        "price_cents": 7900,
        "member_price_cents": 6900,
        "stock": 97,
        "ship_from": "上海",
        "description": "双向自动开合，晴雨两用，通勤便携。",
        "rich_text": "伞骨：8 骨；重量：320g；防晒：UPF50+；颜色：雾蓝",
        "attributes": {"weight_g": 320, "upf": 50, "auto": True, "color": "雾蓝"},
        "tags": ["晴雨伞", "折叠伞", "通勤", "防晒"],
    },
    {
        "sku": "CHG-65W-GAN",
        "name": "65W 氮化镓快充充电器",
        "category": "充电器",
        "price_cents": 14900,
        "member_price_cents": 13900,
        "stock": 188,
        "ship_from": "深圳",
        "description": "双口快充，可为笔记本和手机同时供电。",
        "rich_text": "功率：65W；接口：2C1A；协议：PD/PPS/QC；重量：105g",
        "attributes": {"power_w": 65, "ports": 3, "weight_g": 105},
        "tags": ["充电器", "氮化镓", "65W", "笔记本充电"],
    },
    {
        "sku": "EAR-NC-BLK",
        "name": "主动降噪真无线耳机",
        "category": "耳机",
        "price_cents": 39900,
        "member_price_cents": 37900,
        "stock": 52,
        "ship_from": "东莞",
        "description": "通勤降噪，双设备连接，续航 30 小时。",
        "rich_text": "降噪：42dB；蓝牙：5.4；续航：30h；颜色：曜石黑",
        "attributes": {"anc_db": 42, "battery_hours": 30, "bluetooth": "5.4"},
        "tags": ["耳机", "降噪", "通勤", "蓝牙"],
    },
    {
        "sku": "CUP-500-WHT",
        "name": "500ml 真空保温杯",
        "category": "保温杯",
        "price_cents": 11900,
        "member_price_cents": 10900,
        "stock": 210,
        "ship_from": "上海",
        "description": "轻量保温，单手开盖，适合办公室。",
        "rich_text": "容量：500ml；保温：12h；重量：260g；颜色：暖白",
        "attributes": {"volume_ml": 500, "keep_warm_hours": 12, "weight_g": 260},
        "tags": ["保温杯", "办公室", "便携"],
    },
    {
        "sku": "PIL-BLK",
        "name": "记忆棉便携颈枕",
        "category": "颈枕",
        "price_cents": 9900,
        "member_price_cents": 8900,
        "stock": 76,
        "ship_from": "苏州",
        "description": "慢回弹记忆棉，收纳体积小，适合差旅。",
        "rich_text": "填充：记忆棉；外套：可拆洗；重量：280g；颜色：深灰",
        "attributes": {"filling": "记忆棉", "weight_g": 280, "scene": ["差旅"]},
        "tags": ["颈枕", "差旅", "便携"],
    },
]


def seed_database() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.scalar(select(Product.id).limit(1)):
            return
        for item in PRODUCTS:
            db.add(Product(**item))
        db.add_all(
            [
                User(
                    id="user-buyer-demo",
                    name="林夏",
                    role="buyer",
                    member=True,
                    shipping_address="上海市浦东新区演示路 88 号",
                ),
                User(
                    id="user-merchant-demo",
                    name="店铺运营",
                    role="merchant",
                    member=False,
                    shipping_address="杭州市滨江区演示街 6 号",
                ),
                Coupon(
                    code="MEMBER50",
                    name="会员满 199 减 50",
                    discount_cents=5000,
                    min_spend_cents=19900,
                    member_only=True,
                ),
                Coupon(
                    code="FALL30",
                    name="秋日满 129 减 30",
                    discount_cents=3000,
                    min_spend_cents=12900,
                    member_only=False,
                ),
            ]
        )
        seed_behavior(db)
        db.flush()
        seeded_change = ChangeOrder(
            kind="price_change",
            sku="BAG-16-GRY",
            payload={"new_price_cents": 29900, "effective_from": "today"},
            before_snapshot={"price_cents": 31900, "member_price_cents": 29900},
            expected_gmv_delta_cents=-184000,
            impact_scope={"sku_count": 1, "time_window": "7d", "expected_daily_orders": 11},
            status="executed",
            approval_required=True,
            created_by="user-merchant-demo",
        )
        db.add(seeded_change)
        db.flush()
        db.add(
            ExperimentResult(
                name="通勤包降价 + 首页卡片联动实验",
                change_order_id=seeded_change.id,
                control_visitors=2840,
                control_conversions=113,
                treatment_visitors=2816,
                treatment_conversions=147,
                refund_rate_delta=-0.004,
            )
        )
        db.add(
            AuditLog(
                actor_id="user-merchant-demo",
                action="execute_change",
                target=seeded_change.id,
                before=seeded_change.before_snapshot,
                after={"price_cents": 29900, "member_price_cents": 29900},
            )
        )
        db.commit()


def seed_behavior(db: Session) -> None:
    today = date.today()
    base = {
        "BAG-16-BLK": (780, 142, 58, 27, 2),
        "BAG-16-GRY": (530, 106, 47, 23, 2),
        "BAG-14-CRM": (410, 82, 36, 18, 1),
        "SUN-50-MLD": (920, 171, 88, 51, 3),
        "SUN-30-WATER": (610, 101, 54, 29, 2),
        "UMB-AUTO-BLU": (340, 73, 31, 17, 1),
    }
    for offset in range(1, 15):
        event_day = today - timedelta(days=offset)
        for sku, counts in base.items():
            impression, click, add_cart, pay, refund = counts
            factor = 0.92 + ((offset * 7 + len(sku)) % 13) / 100
            if sku == "BAG-16-GRY" and offset in (1, 2):
                factor *= 0.48
            stages = {
                "impression": (round(impression * factor), 0),
                "click": (round(click * factor), 0),
                "add_cart": (round(add_cart * factor), 0),
                "pay": (round(pay * factor), round(pay * factor) * 31_900),
                "refund": (refund, refund * 31_900),
            }
            for stage, (count, amount) in stages.items():
                db.add(
                    BehaviorEvent(
                        event_date=event_day,
                        sku=sku,
                        stage=stage,
                        count=max(count, 0),
                        amount_cents=amount,
                    )
                )


if __name__ == "__main__":
    seed_database()
    print("Demo database seeded.")
