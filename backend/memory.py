"""Redis-backed short-term memory for the travel agent."""

from __future__ import annotations

import json
import os
from typing import Any

import redis


class TravelMemory:
    """Stores conversation messages and structured tool results per session."""

    def __init__(
        self,
        redis_url: str | None = None,
        prefix: str = "travel",
        ttl_seconds: int = 60 * 60 * 6,  # 6 hours
    ):
        url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.client = redis.from_url(url, decode_responses=True)
        self.prefix = prefix
        self.ttl = ttl_seconds

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _key(self, session_id: str, name: str) -> str:
        return f"{self.prefix}:{session_id}:{name}"

    def _touch(self, session_id: str) -> None:
        """Refresh TTL on all keys for this session."""
        for name in (
            "messages",
            "last_flights",
            "last_hotels",
            "last_attractions",
            "last_restaurants",
            "meta",
        ):
            key = self._key(session_id, name)
            if self.client.exists(key):
                self.client.expire(key, self.ttl)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def get_messages(self, session_id: str) -> list[dict[str, str]]:
        raw = self.client.get(self._key(session_id, "messages"))
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    def save_messages(self, session_id: str, messages: list[dict[str, str]]) -> None:
        self.client.set(
            self._key(session_id, "messages"),
            json.dumps(messages, ensure_ascii=False),
            ex=self.ttl,
        )
        self._touch(session_id)

    def append_message(self, session_id: str, role: str, content: str) -> None:
        messages = self.get_messages(session_id)
        messages.append({"role": role, "content": content})
        self.save_messages(session_id, messages)

    # ------------------------------------------------------------------
    # Structured tool results
    # ------------------------------------------------------------------

    def save_tool_result(
        self, session_id: str, tool_name: str, result: dict[str, Any]
    ) -> None:
        mapping = {
            "search_flights": "last_flights",
            "search_hotels": "last_hotels",
            "search_attractions": "last_attractions",
            "search_restaurants": "last_restaurants",
        }
        name = mapping.get(tool_name)
        if not name:
            return
        self.client.set(
            self._key(session_id, name),
            json.dumps(result, ensure_ascii=False),
            ex=self.ttl,
        )
        self._touch(session_id)

    def get_tool_result(self, session_id: str, tool_name: str) -> dict[str, Any] | None:
        mapping = {
            "search_flights": "last_flights",
            "search_hotels": "last_hotels",
            "search_attractions": "last_attractions",
            "search_restaurants": "last_restaurants",
        }
        name = mapping.get(tool_name)
        if not name:
            return None
        raw = self.client.get(self._key(session_id, name))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    def get_all_tool_results(self, session_id: str) -> dict[str, Any]:
        """Return all stored structured results for this session."""
        return {
            "flights": self.get_tool_result(session_id, "search_flights"),
            "hotels": self.get_tool_result(session_id, "search_hotels"),
            "attractions": self.get_tool_result(session_id, "search_attractions"),
            "restaurants": self.get_tool_result(session_id, "search_restaurants"),
        }

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def clear_session(self, session_id: str) -> None:
        keys = [
            self._key(session_id, name)
            for name in (
                "messages",
                "last_flights",
                "last_hotels",
                "last_attractions",
                "last_restaurants",
                "meta",
            )
        ]
        if keys:
            self.client.delete(*keys)

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False