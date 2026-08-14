"""配置管理：读写 config/config.json，与 adobe2api 的 config 格式完全兼容。

adobe2api 字段（已兼容）：
  api_key, admin_username, admin_password, use_proxy, proxy, gpt_image_quality,
  generate_timeout, retry_enabled, retry_max_attempts, retry_backoff_seconds,
  retry_on_status_codes, retry_on_error_types, token_rotation_strategy

本画布扩展字段：
  external_base_url   外部 OpenAI 兼容 API 的 Base URL
  external_api_key    外部 API Key
  default_channel     默认通道: "firefly" | "external"
  use_socks5_proxy    是否优先使用独立 SOCKS5 上游代理
  socks5_proxy        SOCKS5 地址，如 socks5://user:pass@host:port
  use_socks5_proxy_chain  是否将 SOCKS5 上游通过本地 HTTP 代理连接
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.parse import urlsplit

from backend.proxy_chain import chained_socks5_relay, validate_http_connect_proxy

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "config.json"

DEFAULTS: dict = {
    "api_key": "",
    "admin_username": "admin",
    "admin_password": "",
    "use_proxy": False,
    "proxy": "",
    "use_socks5_proxy": False,
    "socks5_proxy": "",
    "use_socks5_proxy_chain": False,
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
        with self._lock:
            return self._data.get(key, default)

    def get_all(self) -> dict:
        with self._lock:
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


_PROXY_RUNTIME_LOCK = threading.RLock()
_PROXY_CONFIG_KEYS = {
    "use_proxy",
    "proxy",
    "use_socks5_proxy",
    "socks5_proxy",
    "use_socks5_proxy_chain",
}


_PROXY_MODES = {"off", "local", "chain", "socks5"}
_PROXY_MODE_ALIASES = {
    "none": "off",
    "direct": "socks5",
    "direct_socks5": "socks5",
    "standalone": "socks5",
}


def normalize_proxy_mode(value: object) -> str:
    """Validate the UI-facing route mode while retaining legacy config keys."""
    mode = str(value or "").strip().lower()
    mode = _PROXY_MODE_ALIASES.get(mode, mode)
    if mode not in _PROXY_MODES:
        raise ValueError("代理模式必须为 off、local、chain 或 socks5")
    return mode


def proxy_mode_settings(mode: object) -> dict[str, bool]:
    """Map the UI-facing mode to the persisted backward-compatible switches."""
    normalized = normalize_proxy_mode(mode)
    return {
        "use_proxy": normalized in {"local", "chain"},
        "use_socks5_proxy": normalized == "socks5",
        "use_socks5_proxy_chain": normalized == "chain",
    }


def get_proxy_mode(data: dict | None = None) -> str:
    """Derive one unambiguous route mode from existing persisted switches."""
    source = data if data is not None else config_manager.get_all()
    if source.get("use_socks5_proxy_chain"):
        return "chain"
    if source.get("use_socks5_proxy"):
        return "socks5"
    if source.get("use_proxy"):
        return "local"
    return "off"


def get_proxy_chain_traffic() -> dict[str, int | bool]:
    """Return this backend run's traffic counters for the local chain relay."""
    return chained_socks5_relay.traffic_snapshot()


def validate_socks5_proxy(value: object) -> str:
    """Validate a standalone SOCKS5 proxy URL without exposing its credentials."""
    proxy = str(value or "").strip()
    if not proxy:
        return ""

    parsed = urlsplit(proxy)
    if parsed.scheme.lower() not in {"socks5", "socks5h"}:
        raise ValueError("SOCKS5 代理必须以 socks5:// 或 socks5h:// 开头")
    if not parsed.hostname:
        raise ValueError("SOCKS5 代理缺少主机地址")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("SOCKS5 代理端口无效") from exc
    if port is None or not 1 <= port <= 65535:
        raise ValueError("SOCKS5 代理必须包含 1-65535 之间的端口")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("SOCKS5 代理地址不能包含路径、查询参数或片段")
    return proxy


def _configured_proxy(enabled_key: str, url_key: str, data: dict | None = None) -> str:
    source = data if data is not None else config_manager.get_all()
    if not source.get(enabled_key):
        return ""
    return str(source.get(url_key) or "").strip()


def get_active_proxy() -> tuple[str, str]:
    """Return the active outbound proxy, preferring an explicit proxy chain."""
    with _PROXY_RUNTIME_LOCK:
        data = config_manager.get_all()
        if data.get("use_socks5_proxy_chain"):
            local_proxy = _configured_proxy("use_proxy", "proxy", data)
            socks5_proxy = str(data.get("socks5_proxy") or "").strip()
            if local_proxy and socks5_proxy:
                return "chain", chained_socks5_relay.ensure(local_proxy, socks5_proxy)

        socks5_proxy = _configured_proxy("use_socks5_proxy", "socks5_proxy", data)
        if socks5_proxy:
            return "socks5", socks5_proxy
        local_proxy = _configured_proxy("use_proxy", "proxy", data)
        if local_proxy:
            return "local", local_proxy
        return "", ""


def get_requests_proxies(kind: str | None = None) -> dict[str, str] | None:
    """Build the requests proxy mapping for the active or a named proxy."""
    with _PROXY_RUNTIME_LOCK:
        data = config_manager.get_all()
        if kind == "local":
            proxy = _configured_proxy("use_proxy", "proxy", data)
        elif kind == "socks5":
            proxy = _configured_proxy("use_socks5_proxy", "socks5_proxy", data)
        elif kind == "chain":
            if not data.get("use_socks5_proxy_chain"):
                proxy = ""
            else:
                local_proxy = _configured_proxy("use_proxy", "proxy", data)
                socks5_proxy = str(data.get("socks5_proxy") or "").strip()
                proxy = (
                    chained_socks5_relay.ensure(local_proxy, socks5_proxy)
                    if local_proxy and socks5_proxy
                    else ""
                )
        elif kind is None:
            _, proxy = get_active_proxy()
        else:
            raise ValueError(f"unknown proxy kind: {kind}")
        return {"http": proxy, "https": proxy} if proxy else None


def update_config_and_invalidate_proxy_chain(patch: dict) -> dict:
    """Apply a config patch without exposing callers to a stale relay port."""
    with _PROXY_RUNTIME_LOCK:
        config = config_manager.update(patch)
        if _PROXY_CONFIG_KEYS & patch.keys():
            chained_socks5_relay.invalidate()
        return config


def invalidate_proxy_chain() -> None:
    """Stop an old relay after a relevant proxy setting changes."""
    with _PROXY_RUNTIME_LOCK:
        chained_socks5_relay.invalidate()
