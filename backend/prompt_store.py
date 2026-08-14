"""提示词库存储：data/prompts.json

prompt 条目格式：
  {
    "id": "uuid",
    "title": "标题",
    "content": "提示词内容",
    "category": "分类",
    "tags": ["标签"],
    "created_at": 1720000000,
    "updated_at": 1720000000
  }
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPTS_FILE = BASE_DIR / "data" / "prompts.json"

_lock = threading.Lock()


def _ensure_file() -> None:
    PROMPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not PROMPTS_FILE.exists():
        PROMPTS_FILE.write_text(
            json.dumps({"prompts": []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _load_locked() -> list[dict]:
    try:
        raw = json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))
        return list(raw.get("prompts", []))
    except Exception:
        return []


def _save_locked(prompts: list[dict]) -> None:
    PROMPTS_FILE.write_text(
        json.dumps({"prompts": prompts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_prompts(category: str = "") -> list[dict]:
    with _lock:
        _ensure_file()
        prompts = _load_locked()
        if category:
            prompts = [p for p in prompts if p.get("category") == category]
        prompts.sort(key=lambda p: p.get("updated_at", 0), reverse=True)
        return prompts


def add_prompt(title: str, content: str, category: str = "", tags: list | None = None) -> dict:
    with _lock:
        _ensure_file()
        prompts = _load_locked()
        now = int(time.time())
        item = {
            "id": f"prm_{uuid.uuid4().hex[:10]}",
            "title": (title or "").strip()[:100],
            "content": (content or "").strip(),
            "category": (category or "").strip()[:50],
            "tags": tags or [],
            "created_at": now,
            "updated_at": now,
        }
        prompts.append(item)
        _save_locked(prompts)
        return item


def update_prompt(prompt_id: str, fields: dict) -> dict | None:
    with _lock:
        _ensure_file()
        prompts = _load_locked()
        for p in prompts:
            if p["id"] == prompt_id:
                for key in ("title", "content", "category", "tags"):
                    if key in fields and fields[key] is not None:
                        p[key] = fields[key]
                p["updated_at"] = int(time.time())
                _save_locked(prompts)
                return p
        return None


def delete_prompt(prompt_id: str) -> bool:
    with _lock:
        _ensure_file()
        prompts = _load_locked()
        before = len(prompts)
        prompts = [p for p in prompts if p["id"] != prompt_id]
        if len(prompts) == before:
            return False
        _save_locked(prompts)
        return True
