from __future__ import annotations

from fastapi.testclient import TestClient


def test_merchant_funnel_finds_seeded_anomaly(
    client: TestClient, merchant_headers: dict[str, str]
) -> None:
    response = client.get("/api/merchant/diagnose", headers=merchant_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["primary"]["sku"] == "BAG-16-GRY"
    assert payload["primary"]["delta_pct"] < -40
    assert payload["primary"]["evidence_strength"] in {"medium", "high"}
    assert payload["guardrail"].startswith("模型不直接计算指标")


def test_change_order_requires_approval_executes_and_rolls_back(
    client: TestClient, merchant_headers: dict[str, str]
) -> None:
    created = client.post(
        "/api/merchant/changes",
        headers=merchant_headers,
        json={"kind": "price_change", "sku": "BAG-16-GRY", "new_price_cents": 28900},
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["approval_required"] is True
    assert payload["status"] == "pending_approval"
    change_id = payload["change_order_id"]

    blocked = client.post(f"/api/merchant/changes/{change_id}/execute", headers=merchant_headers)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "APPROVAL_REQUIRED"

    assert (
        client.post(f"/api/merchant/changes/{change_id}/approve", headers=merchant_headers).json()[
            "status"
        ]
        == "approved"
    )
    assert (
        client.post(f"/api/merchant/changes/{change_id}/execute", headers=merchant_headers).json()[
            "status"
        ]
        == "executed"
    )
    assert (
        client.post(f"/api/merchant/changes/{change_id}/rollback", headers=merchant_headers).json()[
            "status"
        ]
        == "rolled_back"
    )


def test_tool_gateway_exposes_14_tools_and_enforces_scope(
    client: TestClient, buyer_headers: dict[str, str]
) -> None:
    listed = client.get("/api/tools", headers=buyer_headers)
    assert listed.status_code == 200
    assert listed.json()["count"] == 14
    assert len(listed.json()["items"]) == 14

    result = client.post(
        "/api/tools/call",
        headers=buyer_headers,
        json={"name": "catalog.search", "arguments": {"query": "防晒", "limit": 3}},
    )
    assert result.status_code == 200
    assert result.json()["ok"] is True
    assert result.json()["data"]["items"][0]["sku"].startswith("SUN-")

    forbidden = client.post(
        "/api/tools/call",
        headers=buyer_headers,
        json={"name": "merchant.metrics", "arguments": {"days": 7}},
    )
    assert forbidden.status_code == 200
    assert forbidden.json()["code"] == "TOOL_SCOPE_FORBIDDEN"

    cart_payload = {
        "name": "cart.upsert",
        "arguments": {
            "sku": "BAG-16-BLK",
            "quantity": 1,
            "idempotency_key": "pytest-cart-idempotency",
        },
    }
    first_cart = client.post("/api/tools/call", headers=buyer_headers, json=cart_payload)
    second_cart = client.post("/api/tools/call", headers=buyer_headers, json=cart_payload)
    assert first_cart.json()["data"].get("replayed") is not True
    assert second_cart.json()["data"]["replayed"] is True


def test_experiment_and_daily_report_are_available(
    client: TestClient, merchant_headers: dict[str, str]
) -> None:
    experiments = client.get("/api/merchant/experiments", headers=merchant_headers)
    assert experiments.status_code == 200
    assert experiments.json()["experiments"][0]["relative_lift"] > 0
    report = client.get("/api/merchant/report", headers=merchant_headers)
    assert report.status_code == 200
    assert "GMV" in report.json()["headline"]
