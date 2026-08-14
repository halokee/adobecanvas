"""Token 池管理：读写 config/tokens.json，与 adobe2api 的 token 格式完全兼容。

token 条目格式（兼容 adobe2api）：
  {
    "id": "uuid",
    "value": "IMS access token",
    "status": "valid" | "invalid",
    "fails": 0,
    "added_at": 1720000000,
    "name": "可选名称",
    "auto_refresh": true,
    "expiry": 1720000000 | null,
    "profile_id": "关联的 cookie 刷新配置 id"
  }
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
TOKENS_FILE = BASE_DIR / "config" / "tokens.json"


class TokenManager:
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._tokens: list[dict] = []
        self._round_robin_idx = 0
        self.load()

    # ---------- 存储 ----------
    def load(self) -> None:
        with self._lock:
            if TOKENS_FILE.exists():
                try:
                    raw = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        self._tokens = [t for t in raw if isinstance(t, dict)]
                except Exception:
                    self._tokens = []
            self._prune_expired_locked()

    def _save_locked(self) -> None:
        TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKENS_FILE.write_text(
            json.dumps(self._tokens, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _prune_expired_locked(self) -> None:
        now = time.time()
        changed = False
        kept = []
        for t in self._tokens:
            expiry = t.get("expiry")
            if isinstance(expiry, (int, float)) and expiry and expiry < now:
                # 过期 token：若关联 cookie 可刷新则标记待刷新，否则丢弃
                if t.get("profile_id"):
                    t["status"] = "refresh_pending"
                    kept.append(t)
                    changed = True
                continue
            kept.append(t)
        self._tokens = kept
        if changed:
            self._save_locked()

    # ---------- 查询 ----------
    def all(self) -> list[dict]:
        with self._lock:
            self._prune_expired_locked()
            return list(self._tokens)

    def count(self) -> int:
        return len(self.all())

    def get(self, token_id: str) -> Optional[dict]:
        with self._lock:
            for t in self._tokens:
                if t.get("id") == token_id:
                    return dict(t)
            return None

    # ---------- 写入 ----------
    def add(self, value: str, name: Optional[str] = None,
            profile_id: Optional[str] = None,
            expiry: Optional[int] = None,
            auto_refresh: bool = True) -> dict:
        """新增一个 token（IMS token）。value 为空则忽略。"""
        value = str(value or "").strip()
        if not value:
            raise ValueError("token value is empty")
        entry: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "value": value,
            "status": "valid",
            "fails": 0,
            "added_at": int(time.time()),
            "name": str(name or "").strip() or f"token-{int(time.time())}",
            "auto_refresh": bool(auto_refresh),
            "expiry": int(expiry) if expiry else None,
        }
        if profile_id:
            entry["profile_id"] = profile_id
        with self._lock:
            self._tokens.append(entry)
            self._save_locked()
            return dict(entry)

    def update_status(self, token_id: str, status: str) -> None:
        with self._lock:
            for t in self._tokens:
                if t.get("id") == token_id:
                    t["status"] = status
                    break
            self._save_locked()

    def mark_fail(self, token_id: str) -> None:
        with self._lock:
            for t in self._tokens:
                if t.get("id") == token_id:
                    t["fails"] = int(t.get("fails") or 0) + 1
                    if t["fails"] >= 5:
                        t["status"] = "invalid"
                    break
            self._save_locked()

    def mark_success(self, token_id: str) -> None:
        with self._lock:
            for t in self._tokens:
                if t.get("id") == token_id:
                    t["fails"] = 0
                    if t.get("status") in ("invalid", "refresh_pending"):
                        t["status"] = "valid"
                    break
            self._save_locked()

    def remove(self, token_id: str) -> None:
        with self._lock:
            self._tokens = [t for t in self._tokens if t.get("id") != token_id]
            self._save_locked()

    def remove_batch(self, ids: list[str]) -> None:
        s = set(ids)
        with self._lock:
            self._tokens = [t for t in self._tokens if t.get("id") not in s]
            self._save_locked()

    def update_expiry(self, token_id: str, expiry: Optional[int]) -> None:
        with self._lock:
            for t in self._tokens:
                if t.get("id") == token_id:
                    t["expiry"] = int(expiry) if expiry else None
                    break
            self._save_locked()

    def update_auto_refresh(self, token_id: str, enabled: bool) -> None:
        with self._lock:
            for t in self._tokens:
                if t.get("id") == token_id:
                    t["auto_refresh"] = bool(enabled)
                    break
            self._save_locked()

    # ---------- credits ----------
    def set_credits(self, token_id: str, credits: dict) -> None:
        with self._lock:
            for t in self._tokens:
                if t.get("id") == token_id:
                    t["credits"] = dict(credits)
                    break
            self._save_locked()

    def total_credits(self) -> dict:
        """汇总所有 token 的可用积分。"""
        total = 0
        used = 0
        with self._lock:
            for t in self._tokens:
                c = t.get("credits")
                if isinstance(c, dict):
                    avail = c.get("available")
                    if isinstance(avail, (int, float)):
                        total += avail
                    u = c.get("used")
                    if isinstance(u, (int, float)):
                        used += u
        return {"total_available": total, "total_used": used}

    # ---------- 取用 ----------
    def next_valid(self, strategy: str = "round_robin") -> Optional[dict]:
        """按策略取出一个有效 token；无有效 token 返回 None。"""
        with self._lock:
            self._prune_expired_locked()
            valid = [t for t in self._tokens if t.get("status") == "valid"]
            if not valid:
                return None
            if strategy == "random":
                import random

                return dict(random.choice(valid))
            # round_robin
            idx = self._round_robin_idx % len(valid)
            chosen = dict(valid[idx])
            self._round_robin_idx += 1
            return chosen

    def refresh_pending(self) -> list[dict]:
        with self._lock:
            return [dict(t) for t in self._tokens
                    if t.get("status") == "refresh_pending" and t.get("profile_id")]


token_manager = TokenManager()
