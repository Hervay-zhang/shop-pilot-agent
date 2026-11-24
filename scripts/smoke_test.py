from __future__ import annotations

import argparse
import time
import uuid

import httpx


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)
    print(f"PASS  {message}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ShopPilot end-to-end smoke test.")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Application base URL",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    with httpx.Client(timeout=30) as client:
        health = client.get(f"{base}/api/health")
        health.raise_for_status()
        require(health.json()["status"] == "ok", "health endpoint is ready")

        buyer = client.get(f"{base}/api/auth/demo-token", params={"role": "buyer"}).json()[
            "access_token"
        ]
        merchant = client.get(f"{base}/api/auth/demo-token", params={"role": "merchant"}).json()[
            "access_token"
        ]
        buyer_headers = {"Authorization": f"Bearer {buyer}"}
        merchant_headers = {"Authorization": f"Bearer {merchant}"}

        started = client.post(
            f"{base}/api/buyer/runs",
            headers=buyer_headers,
            json={"message": "预算 800 元，买一个 16 寸电脑通勤包和防晒霜，上海发货"},
        )
        started.raise_for_status()
        run_id = started.json()["run_id"]
        run = {}
        for _ in range(60):
            run = client.get(f"{base}/api/buyer/runs/{run_id}", headers=buyer_headers).json()
            if run["status"] not in {"queued", "running"}:
                break
            time.sleep(0.25)
        require(run.get("status") == "completed", "buyer workflow completes")
        require(run["result"]["requires_confirmation"] is True, "order remains a draft")
        require(run["result"]["price_breakdown"]["total_cents"] == 41800, "price is ¥418")

        draft_id = run["result"]["draft_id"]
        confirm_payload = {
            "draft_id": draft_id,
            "confirmed": True,
            "request_id": f"smoke-{uuid.uuid4().hex}",
            "shipping_address": "上海市浦东新区演示路 88 号",
        }
        first = client.post(
            f"{base}/api/buyer/orders/confirm",
            headers=buyer_headers,
            json=confirm_payload,
        )
        first.raise_for_status()
        second = client.post(
            f"{base}/api/buyer/orders/confirm",
            headers=buyer_headers,
            json=confirm_payload,
        )
        second.raise_for_status()
        require(
            first.json()["order_id"] == second.json()["order_id"],
            "order confirmation is idempotent",
        )

        tools = client.get(f"{base}/api/tools", headers=buyer_headers)
        tools.raise_for_status()
        require(tools.json()["count"] == 14, "14 MCP tools are registered")

        diagnosis = client.get(f"{base}/api/merchant/diagnose", headers=merchant_headers)
        diagnosis.raise_for_status()
        require(
            diagnosis.json()["primary"]["sku"] == "BAG-16-GRY",
            "merchant diagnosis finds the seeded anomaly",
        )

        change = client.post(
            f"{base}/api/merchant/changes",
            headers=merchant_headers,
            json={"kind": "price_change", "sku": "BAG-16-GRY", "new_price_cents": 28900},
        )
        change.raise_for_status()
        change_id = change.json()["change_order_id"]
        require(change.json()["approval_required"] is True, "high-risk change needs approval")
        client.post(
            f"{base}/api/merchant/changes/{change_id}/approve", headers=merchant_headers
        ).raise_for_status()
        client.post(
            f"{base}/api/merchant/changes/{change_id}/execute", headers=merchant_headers
        ).raise_for_status()
        rollback = client.post(
            f"{base}/api/merchant/changes/{change_id}/rollback", headers=merchant_headers
        )
        rollback.raise_for_status()
        require(rollback.json()["status"] == "rolled_back", "merchant action rolls back")

    print(f"\nShopPilot smoke test passed: {base}")


if __name__ == "__main__":
    main()
