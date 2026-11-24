from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "ShopPilot 电商 Agent")
    app_env: str = os.getenv("APP_ENV", "development")
    secret_key: str = os.getenv("SECRET_KEY", "local-demo-secret-change-me")
    demo_mode: bool = os.getenv("DEMO_MODE", "true").lower() == "true"
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/shop_pilot.db")
    redis_url: str | None = os.getenv("REDIS_URL") or None
    qdrant_url: str | None = os.getenv("QDRANT_URL") or None
    deepseek_api_key: str | None = os.getenv("DEEPSEEK_API_KEY") or None
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    llm_models: tuple[str, ...] = tuple(
        item.strip()
        for item in os.getenv("LLM_MODELS", "deepseek-chat,deepseek-reasoner").split(",")
        if item.strip()
    )
    max_request_cents: int = int(os.getenv("MAX_REQUEST_CENTS", "200000"))
    high_value_order_cents: int = int(os.getenv("HIGH_VALUE_ORDER_CENTS", "200000"))
    approval_threshold_cents: int = int(os.getenv("APPROVAL_THRESHOLD_CENTS", "500000"))


settings = Settings()

if settings.database_url.startswith("sqlite:///"):
    (ROOT_DIR / "data").mkdir(exist_ok=True)
