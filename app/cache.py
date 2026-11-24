from __future__ import annotations

import json
import threading
import time
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.config import settings


class CacheService:
    """Redis-backed cache with a process-local fallback for a zero-config demo."""

    def __init__(self) -> None:
        self._redis: Redis | None = None
        self._local: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()
        if settings.redis_url:
            self._redis = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.25,
                socket_timeout=0.25,
            )

    @property
    def backend(self) -> str:
        if self._redis:
            try:
                self._redis.ping()
                return "redis"
            except RedisError:
                pass
        return "memory"

    def get_json(self, key: str) -> Any | None:
        if self._redis:
            try:
                raw = self._redis.get(key)
                return json.loads(raw) if raw else None
            except RedisError:
                pass
        with self._lock:
            item = self._local.get(key)
            if not item:
                return None
            expires_at, raw = item
            if expires_at < time.time():
                self._local.pop(key, None)
                return None
            return json.loads(raw)

    def set_json(self, key: str, value: Any, ttl_seconds: int = 1800) -> None:
        raw = json.dumps(value, ensure_ascii=False)
        if self._redis:
            try:
                self._redis.setex(key, ttl_seconds, raw)
                return
            except RedisError:
                pass
        with self._lock:
            self._local[key] = (time.time() + ttl_seconds, raw)

    def delete(self, key: str) -> None:
        if self._redis:
            try:
                self._redis.delete(key)
                return
            except RedisError:
                pass
        with self._lock:
            self._local.pop(key, None)


cache = CacheService()
