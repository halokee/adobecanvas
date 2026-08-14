"""配置管理：读写 config/config.json，与 adobe2api 的 config 格式完全兼容。

adobe2api 字段（已兼容）：
  api_key, admin_username, admin_password, use_proxy, proxy, gpt_image_quality,
  generate_timeout, retry_enabled, retry_max_attempts, retry_backoff_seconds,
  retry_on_status_codes, retry_on_error_types, token_rotation_strategy

本画布扩展字段：
  external_base_url   外部 OpenAI 兼容 API 的 Base URL
  external_api_key    外部 API Key
  default_channel     默认通道: "firefly" | "external"
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "config.json"

DEFAULTS: dict = {
    "api_key": "",
    "admin_username": "admin",
    "admin_password": "",
    "use_proxy": False,
    "proxy": "",
    "gpt_image_quality": "low",
    "generate_timeout": 300,
    "retry_enabled": True,
    "retry_max_attempts": 3,
    "retry_backoff_seconds": 1.0,
    "retry_on_status_codes": [429, 451, 500, 502, 503, 504],
    "retry_on_error_types": ["timeout", "connection", "proxy"],
    "token_rotation_strategy": "round_robin",
    "external_base_url": "",
    "external_api_key": "",
    "default_channel": "firefly",
}


class ConfigManager:
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._data: dict = dict(DEFAULTS)
        self.load()

    def load(self) -> dict:
        with self._lock:
            if CONFIG_FILE.exists():
                try:
                    raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        merged = dict(DEFAULTS)
                        merged.update({k: v for k, v in raw.items() if v is not None})
                        self._data = merged
                except Exception:
                    pass
            return dict(self._data)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def get_all(self) -> dict:
        return dict(self._data)

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
            self._save_locked()

    def update(self, patch: dict) -> dict:
        with self._lock:
            for k, v in patch.items():
                if v is not None:
                    self._data[k] = v
            self._save_locked()
            return dict(self._data)

    def _save_locked(self) -> None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
        )


config_manager = ConfigManager()
