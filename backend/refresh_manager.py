"""Cookie 管理与刷新：兼容 adobe2api 的 cookie 导入方式。

Cookie 支持的输入格式（与 adobe2api / 浏览器插件一致）：
  1. 字符串:      "name=value; name2=value2"
  2. 简单对象:    {"cookie": "name=value; ..."}
  3. cookie 数组: {"cookies": ["name=value", "name2=value2"]}
  4. 插件标准:    [{"name": "adobe_token", "value": "..."}, ...]  (对象数组)

刷新流程（与 adobe2api 一致）：
  用 cookie 请求 Adobe ID token 端点 -> 获得 IMS access_token -> 存入 token 池。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from backend.token_manager import token_manager

logger = logging.getLogger("local-canvas")

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILES_FILE = BASE_DIR / "config" / "refresh_profiles.json"

DEFAULT_REFRESH_URL = (
    "https://adobeid-na1.services.adobe.com/ims/check/v6/token?jslVersion=v2-v0.48.0-1-g1e322cb"
)
DEFAULT_SCOPE = (
    "AdobeID,firefly_api,openid,pps.read,pps.write,additional_info.projectedProductContext,"
    "additional_info.ownerOrg,uds_read,uds_write,ab.manage,read_organizations,"
    "additional_info.roles,account_cluster.read,creative_production,profile"
)


def decode_jwt_payload(token: str) -> Dict[str, Any]:
    """从 IMS token (JWT) 中解析 claims。"""
    raw = str(token or "").strip()
    if not raw:
        return {}
    parts = raw.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    pad = "=" * (-len(payload) % 4)
    try:
        import base64

        data = base64.urlsafe_b64decode(payload + pad)
        return json.loads(data)
    except Exception:
        return {}


def cookie_pairs_from_input(cookie_input: Any) -> str:
    """把任意支持的 cookie 输入格式转成 "k=v; k2=v2" 字符串。"""
    def _join(pairs) -> str:
        return "; ".join(f"{k}={v}" for k, v in pairs if k and v)

    raw = cookie_input
    if isinstance(raw, str):
        return raw.strip()

    if isinstance(raw, dict):
        for key in ("cookie", "Cookie", "cookieString"):
            if raw.get(key):
                return str(raw[key]).strip()
        if raw.get("cookies"):
            cookies_val = raw["cookies"]
            # 如果 cookies 是字符串，它已经是一个完整的 cookie 字符串
            if isinstance(cookies_val, str):
                return cookies_val.strip()
            return _join(_parse_cookie_list(cookies_val))
        return ""

    if isinstance(raw, (list, tuple)):
        return _join(_parse_cookie_list(raw))

    return ""


def _parse_cookie_list(items: Any) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in items or []:
        if isinstance(item, str):
            for seg in item.split(";"):
                seg = seg.strip()
                if "=" in seg:
                    k, _, v = seg.partition("=")
                    pairs.append((k.strip(), v.strip()))
        elif isinstance(item, dict):
            name = item.get("name") or item.get("key")
            value = item.get("value")
            if name and value:
                pairs.append((str(name).strip(), str(value).strip()))
    return pairs


def parse_cookies_batch(raw: Any) -> list[str]:
    """把可能包含多个 cookie 的输入拆成多个独立 cookie 字符串。

    支持：
      1. 多行文本（每行一个 cookie）
      2. JSON 数组，元素为字符串
      3. JSON 数组，元素为 {"cookie": "..."} / {"cookies": [...]} 等对象
      4. 插件标准对象数组 [{"name","value"}, ...] -> 视为单个 cookie
      5. {"cookies": [...]} 包装
    """
    # 兼容包装对象
    if isinstance(raw, dict) and "cookies" in raw and len(raw) == 1:
        return parse_cookies_batch(raw["cookies"])

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        # 文本本身是 JSON -> 递归解析
        if text[:1] in ("[", "{"):
            try:
                return parse_cookies_batch(json.loads(text))
            except Exception:
                pass
        # 按行拆分，忽略空行与注释行
        lines = [
            ln.strip()
            for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith(("#", "//"))
        ]
        return lines

    if isinstance(raw, list):
        # 插件标准：整组 {name,value} 属于同一个 cookie
        if raw and all(
            isinstance(i, dict) and ("name" in i or "key" in i) and "value" in i
            for i in raw
        ):
            pairs = _parse_cookie_list(raw)
            if pairs:
                return [cookie_pairs_from_input(raw)]
            return []
        results: list[str] = []
        for item in raw:
            if isinstance(item, str):
                if "=" in item or ";" in item:
                    results.append(item.strip())
            elif isinstance(item, dict):
                # 优先处理浏览器插件导出的 {"cookies": "字符串"} 格式
                if "cookies" in item and isinstance(item["cookies"], str):
                    c = item["cookies"].strip()
                    if c:
                        results.append(c)
                else:
                    c = cookie_pairs_from_input(item)
                    if c:
                        results.append(c)
        return results

    if isinstance(raw, dict):
        c = cookie_pairs_from_input(raw)
        return [c] if c else []

    return []


class RefreshManager:
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._profiles: list[dict] = []
        self.load()

    # ---------- 存储 ----------
    def load(self) -> None:
        with self._lock:
            if PROFILES_FILE.exists():
                try:
                    raw = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        self._profiles = [p for p in raw if isinstance(p, dict)]
                except Exception:
                    self._profiles = []

    def _save_locked(self) -> None:
        PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROFILES_FILE.write_text(
            json.dumps(self._profiles, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ---------- 查询 ----------
    def list_profiles(self) -> list[dict]:
        with self._lock:
            return [
                {k: v for k, v in p.items() if k not in ("cookie",)}
                for p in self._profiles
            ]

    def get_profile(self, profile_id: str) -> Optional[dict]:
        with self._lock:
            for p in self._profiles:
                if p.get("id") == profile_id:
                    return dict(p)
            return None

    # ---------- 导入 ----------
    def import_cookie(self, cookie_input: Any, name: Optional[str] = None) -> dict:
        """导入 cookie，立即刷新换取 token。返回 profile + token 信息。"""
        cookie = cookie_pairs_from_input(cookie_input)
        if not cookie:
            raise ValueError("未解析到有效的 cookie，请检查输入格式")

        with self._lock:
            profile_id = str(uuid.uuid4())
            profile: dict[str, Any] = {
                "id": profile_id,
                "name": str(name or "").strip() or f"cookie-{time.strftime('%m%d-%H%M%S')}",
                "cookie": cookie,
                "added_at": int(time.time()),
                "fails": 0,
            }
            self._profiles.append(profile)
            self._save_locked()

        try:
            result = self.refresh_profile(profile_id)
            return {"profile": result["profile"], "token": result["token"]}
        except Exception as exc:
            self.delete_profile(profile_id)
            raise ValueError(f"cookie 导入后刷新失败: {exc}")

    def import_cookies_batch(self, raw: Any) -> dict:
        """批量导入 cookie：解析出多个独立 cookie，逐个导入并刷新换取 token。

        单个失败不影响其他 cookie；失败的会自动回滚已创建的 profile。
        返回 {"total", "ok": [...], "failed": [{"index", "error"}]}
        """
        cookies = parse_cookies_batch(raw)
        if not cookies:
            raise ValueError("未解析到有效的 cookie，请检查输入格式")

        imported: list[dict] = []
        failed: list[dict] = []
        for idx, c in enumerate(cookies):
            try:
                r = self.import_cookie(c, name=f"cookie-{idx + 1}")
                imported.append(r)
            except Exception as exc:
                failed.append({"index": idx, "error": str(exc)})

        return {"total": len(cookies), "ok": imported, "failed": failed}

    # ---------- 刷新 ----------
    def refresh_profile(self, profile_id: str) -> dict:
        profile = self.get_profile(profile_id)
        if not profile:
            raise ValueError("profile not found")

        headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Cookie": profile.get("cookie", ""),
            "Origin": "https://firefly.adobe.com",
            "Referer": "https://firefly.adobe.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
            ),
        }
        form = {
            "client_id": "clio-playground-web",
            "guest_allowed": "true",
            "scope": DEFAULT_SCOPE,
        }
        try:
            resp = requests.post(
                DEFAULT_REFRESH_URL, headers=headers, data=form, timeout=60
            )
        except requests.RequestException as exc:
            self._mark_fail(profile_id)
            raise RuntimeError(f"请求 Adobe token 端点失败: {exc}")

        if resp.status_code not in (200, 201):
            self._mark_fail(profile_id)
            raise RuntimeError(
                f"token 刷新失败 HTTP {resp.status_code}: {resp.text[:300]}"
            )
        try:
            data = resp.json()
        except Exception:
            self._mark_fail(profile_id)
            raise RuntimeError("token 刷新响应不是合法 JSON")

        access_token = str(data.get("access_token") or "").strip()
        if not access_token:
            self._mark_fail(profile_id)
            raise RuntimeError("刷新响应缺少 access_token（cookie 可能已失效）")

        expires_in = int(data.get("expires_in") or 0)
        expiry = int(time.time()) + expires_in if expires_in > 0 else None

        claims = decode_jwt_payload(access_token)
        account_name = (
            str(claims.get("name") or "")
            or str(claims.get("email") or "")
            or profile.get("name", "")
        )

        token_entry = token_manager.add(
            access_token,
            name=account_name,
            profile_id=profile_id,
            expiry=expiry,
            auto_refresh=True,
        )
        self._mark_success(profile_id)
        return {
            "profile": self.get_profile(profile_id),
            "token": token_entry,
        }

    def refresh_pending_tokens(self) -> dict:
        """刷新所有状态为 refresh_pending 的 token（过期但有关联 cookie）。"""
        results = {"ok": [], "failed": []}
        for entry in token_manager.refresh_pending():
            pid = entry.get("profile_id")
            if not pid:
                continue
            try:
                r = self.refresh_profile(pid)
                token_manager.update_status(r["token"]["id"], "valid")
                results["ok"].append(r["token"]["name"])
            except Exception as exc:
                token_manager.update_status(entry["id"], "invalid")
                results["failed"].append(str(exc))
        return results

    # ---------- 删除 ----------
    def delete_profile(self, profile_id: str) -> None:
        with self._lock:
            self._profiles = [p for p in self._profiles if p.get("id") != profile_id]
            self._save_locked()

    def delete_profiles_batch(self, profile_ids: list[str]) -> int:
        ids = set(profile_ids)
        with self._lock:
            before = len(self._profiles)
            self._profiles = [p for p in self._profiles if p.get("id") not in ids]
            self._save_locked()
            return before - len(self._profiles)

    def _mark_fail(self, profile_id: str) -> None:
        with self._lock:
            for p in self._profiles:
                if p.get("id") == profile_id:
                    p["fails"] = int(p.get("fails") or 0) + 1
                    break
            self._save_locked()

    def _mark_success(self, profile_id: str) -> None:
        with self._lock:
            for p in self._profiles:
                if p.get("id") == profile_id:
                    p["fails"] = 0
                    break
            self._save_locked()


refresh_manager = RefreshManager()
