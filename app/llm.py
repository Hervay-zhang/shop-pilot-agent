from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import settings

KNOWN_CITIES = ("上海", "杭州", "北京", "深圳", "广州", "苏州", "成都", "武汉")
CATEGORY_TERMS = {
    "电脑包": ("电脑包", "通勤包", "双肩包", "背包"),
    "防晒": ("防晒霜", "防晒乳", "防晒"),
    "晴雨伞": ("雨伞", "晴雨伞", "折叠伞"),
    "充电器": ("充电器", "快充", "氮化镓"),
    "耳机": ("耳机", "降噪耳机"),
    "保温杯": ("保温杯", "水杯"),
    "颈枕": ("颈枕", "U型枕", "旅行枕"),
}


class RequestParser:
    """Deterministic first, LLM optional. The model never owns filters or price calculations."""

    def parse(self, text: str, user_member: bool) -> dict[str, Any]:
        deterministic = self._deterministic(text, user_member)
        if not settings.deepseek_api_key:
            deterministic["parser"] = "deterministic"
            return deterministic
        llm_result = self._llm_parse(text)
        if not llm_result:
            deterministic["parser"] = "deterministic-fallback"
            return deterministic
        # Rules always win for hard constraints; the model may only fill missing semantic slots.
        for key in ("categories", "budget_cents", "city", "laptop_size"):
            if not deterministic.get(key):
                deterministic[key] = llm_result.get(key)
        deterministic["scene"] = llm_result.get("scene") or deterministic.get("scene")
        deterministic["parser"] = "deepseek+deterministic-guard"
        return deterministic

    @staticmethod
    def _deterministic(text: str, user_member: bool) -> dict[str, Any]:
        categories = [
            category
            for category, terms in CATEGORY_TERMS.items()
            if any(term in text for term in terms)
        ]
        budget_match = re.search(
            r"(?:预算|不超过|控制在)?\s*(\d{2,6})\s*(?:元|块|rmb)(?:以内|以下)?", text, re.I
        )
        if not budget_match:
            budget_match = re.search(r"预算\s*(\d{2,6})", text)
        budget_cents = int(budget_match.group(1)) * 100 if budget_match else 80000
        city = next((item for item in KNOWN_CITIES if item in text), None)
        laptop_match = re.search(r"(\d{2})\s*寸", text)
        laptop_size = int(laptop_match.group(1)) if laptop_match else None
        scene = "通勤" if "通勤" in text else ("差旅" if "差旅" in text else None)
        return {
            "categories": categories,
            "budget_cents": budget_cents,
            "city": city,
            "laptop_size": laptop_size,
            "scene": scene,
            "member": user_member,
            "quantity": 1,
        }

    @staticmethod
    def _llm_parse(text: str) -> dict[str, Any] | None:
        prompt = (
            "你只做电商购物需求槽位抽取。输出 JSON，不要输出 Markdown。"
            "字段: categories(数组), budget_cents(整数或null), city(字符串或null), "
            "laptop_size(整数或null), scene(字符串或null)。"
            f"\n用户需求：{text}"
        )
        for model in settings.llm_models:
            if not model:
                continue
            try:
                response = httpx.post(
                    f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=8,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                payload = json.loads(content)
                return {
                    "categories": [
                        item for item in payload.get("categories", []) if item in CATEGORY_TERMS
                    ],
                    "budget_cents": payload.get("budget_cents"),
                    "city": payload.get("city") if payload.get("city") in KNOWN_CITIES else None,
                    "laptop_size": payload.get("laptop_size"),
                    "scene": payload.get("scene"),
                }
            except Exception:
                continue
        return None


parser = RequestParser()
