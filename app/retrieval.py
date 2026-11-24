from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import BehaviorEvent, Product, User
from app.pricing import unit_price_for

VECTOR_SIZE = 256


@dataclass
class RankedProduct:
    product: Product
    score: float
    lexical_score: float = 0.0
    vector_score: float = 0.0
    behavior_score: float = 0.0

    def explain(self) -> dict[str, Any]:
        return {
            "sku": self.product.sku,
            "name": self.product.name,
            "score": round(self.score, 4),
            "lexical": round(self.lexical_score, 4),
            "vector": round(self.vector_score, 4),
            "behavior": round(self.behavior_score, 4),
        }


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    latin = re.findall(r"[a-z0-9+-]+", lowered)
    chinese = re.findall(r"[\u4e00-\u9fff]", lowered)
    bigrams = ["".join(chinese[index : index + 2]) for index in range(max(0, len(chinese) - 1))]
    return set(latin + chinese + bigrams)


def hash_embedding(text: str, size: int = VECTOR_SIZE) -> list[float]:
    """A deterministic local embedding so the Qdrant path works without a paid API."""
    vector = [0.0] * size
    for token in _tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % size
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


class HybridRetriever:
    def __init__(self) -> None:
        self.client: QdrantClient | None = None
        self.vector_status = "disabled"
        if settings.qdrant_url:
            try:
                self.client = QdrantClient(url=settings.qdrant_url, timeout=0.8)
                self.client.get_collections()
                self.vector_status = "ready"
            except Exception:
                self.client = None
                self.vector_status = "fallback-lexical"

    def sync_products(self, db: Session) -> None:
        if not self.client:
            return
        try:
            collections = {item.name for item in self.client.get_collections().collections}
            if "products_demo" not in collections:
                self.client.create_collection(
                    collection_name="products_demo",
                    vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
                )
            products = db.scalars(select(Product).where(Product.active)).all()
            points = []
            for item in products:
                text = " ".join(
                    [
                        item.name,
                        item.category,
                        item.description,
                        item.rich_text,
                        " ".join(item.tags),
                        " ".join(f"{key}={value}" for key, value in item.attributes.items()),
                    ]
                )
                points.append(
                    PointStruct(
                        id=int(hashlib.md5(item.sku.encode()).hexdigest()[:12], 16),
                        vector=hash_embedding(text),
                        payload={"sku": item.sku, "category": item.category},
                    )
                )
            if points:
                self.client.upsert(collection_name="products_demo", points=points, wait=True)
            self.vector_status = "ready"
        except Exception:
            self.client = None
            self.vector_status = "fallback-lexical"

    def search(
        self,
        db: Session,
        user: User,
        query: str,
        categories: list[str],
        budget_cents: int,
        city: str | None = None,
        laptop_size: int | None = None,
    ) -> dict[str, list[RankedProduct]]:
        products = db.scalars(select(Product).where(Product.active)).all()
        query_tokens = _tokens(query)
        keyword_rank: dict[str, int] = {}
        vector_rank: dict[str, int] = {}

        keyword_scores: dict[str, float] = {}
        for item in products:
            searchable = " ".join(
                [item.name, item.category, item.description, " ".join(item.tags), item.rich_text]
            )
            overlap = query_tokens & _tokens(searchable)
            category_bonus = 7 if item.category in categories else 0
            attr_bonus = (
                3 if laptop_size and item.attributes.get("laptop_size") == laptop_size else 0
            )
            keyword_scores[item.sku] = float(len(overlap) * 2 + category_bonus + attr_bonus)
        keyword_rank = {
            sku: index
            for index, (sku, _) in enumerate(
                sorted(keyword_scores.items(), key=lambda pair: pair[1], reverse=True), start=1
            )
        }

        vector_scores: dict[str, float] = defaultdict(float)
        if self.client:
            try:
                hits = self.client.query_points(
                    collection_name="products_demo",
                    query=hash_embedding(query),
                    limit=20,
                    with_payload=True,
                ).points
                vector_rank = {
                    str(hit.payload["sku"]): index
                    for index, hit in enumerate(hits, start=1)
                    if hit.payload and hit.payload.get("sku")
                }
                vector_scores = {
                    str(hit.payload["sku"]): float(hit.score)
                    for hit in hits
                    if hit.payload and hit.payload.get("sku")
                }
            except Exception:
                self.vector_status = "fallback-lexical"

        behavior_scores = self._behavior_scores(db)
        grouped: dict[str, list[RankedProduct]] = {category: [] for category in categories}
        for item in products:
            if item.category not in categories:
                continue
            if item.stock <= 0 or unit_price_for(item, user.member) > budget_cents:
                continue
            if city and item.ship_from != city:
                continue
            if laptop_size and item.attributes.get("laptop_size") not in (None, laptop_size):
                continue

            lexical = keyword_scores.get(item.sku, 0.0)
            semantic = vector_scores.get(item.sku, 0.0)
            rrf = 0.0
            if item.sku in keyword_rank:
                rrf += 1 / (60 + keyword_rank[item.sku])
            if item.sku in vector_rank:
                rrf += 1 / (60 + vector_rank[item.sku])
            behavioral = behavior_scores.get(item.sku, 0.0)
            score = rrf * 100 + lexical * 0.1 + semantic * 0.4 + behavioral * 0.03
            grouped[item.category].append(
                RankedProduct(
                    product=item,
                    score=score,
                    lexical_score=lexical,
                    vector_score=semantic,
                    behavior_score=behavioral,
                )
            )
        for values in grouped.values():
            values.sort(key=lambda item: item.score, reverse=True)
        return grouped

    @staticmethod
    def _behavior_scores(db: Session) -> dict[str, float]:
        events = db.scalars(
            select(BehaviorEvent).where(BehaviorEvent.stage.in_(["add_cart", "pay"]))
        ).all()
        scores: dict[str, float] = defaultdict(float)
        for event in events:
            weight = 0.35 if event.stage == "add_cart" else 1.0
            scores[event.sku] += event.count * weight
        return dict(scores)


retriever = HybridRetriever()
