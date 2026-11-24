from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DB = Path(__file__).resolve().parent.parent / "data" / "test_shop_pilot.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["REDIS_URL"] = ""
os.environ["QDRANT_URL"] = ""
os.environ["DEMO_MODE"] = "true"

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    TEST_DB.unlink(missing_ok=True)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def buyer_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/auth/demo-token", params={"role": "buyer"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def merchant_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/auth/demo-token", params={"role": "merchant"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
