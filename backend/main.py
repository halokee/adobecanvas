"""本地画布 - FastAPI 后端入口

功能：
  - 服务前端静态资源（web/dist）与生成结果（outputs/）
  - 生成任务管理（后台线程 + 轮询进度）
  - Token / Cookie / 配置管理 API
  - OpenAI 兼容网关（/v1/models、/v1/images/generations、/v1/chat/completions）
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import requests
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.adobe_client import (
    IMAGE_MODELS,
    VIDEO_MODELS,
    AdobeRequestError,
    AuthError,
    adobe_client,
)
from backend.config_manager import (
    config_manager,
    get_active_proxy,
    get_proxy_chain_traffic,
    get_proxy_mode,
    get_requests_proxies,
    proxy_mode_settings,
    update_config_and_invalidate_proxy_chain,
    validate_http_connect_proxy,
    validate_socks5_proxy,
)
from backend.log_store import log_store
from backend.refresh_manager import refresh_manager
from backend.token_manager import token_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("local-canvas")

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = BASE_DIR / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
WEB_DIST = BASE_DIR / "web" / "dist"

app = FastAPI(title="Local Canvas", version="1.0.2")

# The bundled UI is served from this process. Vite development needs the two
# local origins below; accepting arbitrary origins exposes localhost secrets.
LOCAL_ORIGINS = [
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "http://127.0.0.1:8900",
    "http://localhost:8900",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=LOCAL_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


_SENSITIVE_CONFIG_KEYS = {"api_key", "admin_password", "external_api_key", "socks5_proxy"}


def _mask_secret(value: Any) -> str:
    """Keep an identifier for the UI without returning a usable credential."""
    raw = str(value or "")
    if not raw:
        return ""
    if len(raw) <= 12:
        return "********"
    return f"{raw[:8]}...{raw[-4:]}"


def _public_config(data: dict) -> dict:
    public = dict(data)
    for key in _SENSITIVE_CONFIG_KEYS:
        public.pop(key, None)
    try:
        local_proxy_has_credentials = urlsplit(str(data.get("proxy") or "")).username is not None
    except ValueError:
        local_proxy_has_credentials = False
    if local_proxy_has_credentials:
        public.pop("proxy", None)
    public["proxy_configured"] = bool(str(data.get("proxy") or "").strip())
    public["socks5_proxy_configured"] = bool(str(data.get("socks5_proxy") or "").strip())
    public["proxy_mode"] = get_proxy_mode(data)
    return public


def _public_token(entry: dict) -> dict:
    public = dict(entry)
    public["value"] = _mask_secret(public.get("value"))
    return public


def _public_cookie_profile(profile: dict) -> dict:
    return {key: value for key, value in profile.items() if key != "cookie"}


def _public_refresh_result(result: dict) -> dict:
    return {
        "profile": _public_cookie_profile(result.get("profile") or {}),
        "token": _public_token(result.get("token") or {}),
    }


# ---------------- 生成任务管理 ----------------

TASKS: dict[str, dict] = {}
TASKS_LOCK = threading.Lock()


def _new_task(kind: str, params: dict) -> str:
    task_id = str(uuid.uuid4())[:12]
    with TASKS_LOCK:
        TASKS[task_id] = {
            "id": task_id,
            "kind": kind,
            "status": "queued",
            "progress": 0,
            "message": "排队中...",
            "created_at": int(time.time()),
            "assets": [],
            "error": "",
            "params": params,
        }
    return task_id


def _update_task(task_id: str, **fields) -> None:
    with TASKS_LOCK:
        if task_id in TASKS:
            TASKS[task_id].update(fields)


def _on_progress(task_id: str, data: dict):
    status = str(data.get("status") or "").lower()
    progress = data.get("progress")
    message = str(data.get("message") or data.get("error") or "")
    try:
        pct = float(progress) if progress is not None else None
        if pct is not None:
            _update_task(task_id, status="running", progress=min(99, int(pct)),
                         message=message or f"生成中 {int(pct)}%")
        else:
            _update_task(task_id, status="running", message=message or "生成中...")
    except Exception:
        _update_task(task_id, status="running", message="生成中...")


def _run_generation(task_id: str, kind: str, params: dict):
    try:
        if kind == "image":
            result = adobe_client.generate_image(
                prompt=params["prompt"],
                model_id=params.get("model_id", "firefly-nano-banana-pro-2k-16x9"),
                quality=params.get("quality", "medium"),
                init_image_id=params.get("init_image_id"),
                on_progress=lambda d: _on_progress(task_id, d),
            )
        else:  # video
            result = adobe_client.generate_video(
                prompt=params["prompt"],
                model_id=params.get("model_id", "firefly-sora2-8s-16x9-720p"),
                init_image_id=params.get("init_image_id"),
                on_progress=lambda d: _on_progress(task_id, d),
            )
        if not result:
            raise AdobeRequestError("生成完成但未取到结果文件")
        _update_task(task_id, status="done", progress=100, assets=result,
                     message="完成")
    except (AdobeRequestError, AuthError) as exc:
        _update_task(task_id, status="failed", error=str(exc.user_message or exc))
    except Exception as exc:
        logger.exception("generation task failed")
        _update_task(task_id, status="failed", error=f"生成失败: {exc}")


def start_generation(kind: str, params: dict) -> str:
    task_id = _new_task(kind, params)
    threading.Thread(target=_run_generation, args=(task_id, kind, params),
                     daemon=True).start()
    return task_id


# ---------- helpers: credits & logs ----------

def _extract_account_id_from_token(token_value: str) -> str:
    import base64

    try:
        parts = token_value.split(".")
        if len(parts) == 3:
            payload = parts[1]
            pad = 4 - len(payload) % 4
            if pad != 4:
                payload += "=" * pad
            decoded = base64.urlsafe_b64decode(payload)
            data = json.loads(decoded)
            return str(data.get("sub") or data.get("account_id") or "")
    except Exception:
        pass
    return ""


def _fetch_credits_balance(access_token: str, account_id: str) -> dict:
    token = str(access_token or "").strip()
    aid = str(account_id or "").strip()
    if not token or not aid:
        raise RuntimeError("missing token or account_id")
    proxies = adobe_client._proxies()
    resp = requests.get(
        "https://firefly.adobe.io/v1/credits/balance",
        headers={
            "Authorization": f"Bearer {token}",
            "x-api-key": "SunbreakWebUI1",
            "x-account-id": aid,
            "Origin": "https://firefly.adobe.com",
            "Referer": "https://firefly.adobe.com/",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=20,
        proxies=proxies,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"credits request failed: {resp.status_code}")
    try:
        payload = resp.json()
    except Exception:
        raise RuntimeError("credits response invalid json")
    total_info = payload.get("total", {}) if isinstance(payload, dict) else {}
    quota = total_info.get("quota", {}) if isinstance(total_info, dict) else {}
    return {
        "total": quota.get("total"),
        "used": quota.get("used"),
        "available": quota.get("available"),
        "available_until": total_info.get("availableUntil"),
        "updated_at": int(time.time()),
    }


_LOCAL_PROXY_BYPASS_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _is_local_url(url: str) -> bool:
    """Keep loopback OpenAI endpoints direct instead of proxying them back to this app."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    return bool(host and host.lower() in _LOCAL_PROXY_BYPASS_HOSTS)


def _refresh_credits_for_token_id(token_id: str) -> dict:
    token_info = token_manager.get(token_id)
    if not token_info:
        raise KeyError("token not found")
    token_value = str(token_info.get("value") or "").strip()
    account_id = _extract_account_id_from_token(token_value)
    if not account_id:
        raise RuntimeError("unable to extract account_id from token")
    credits = _fetch_credits_balance(token_value, account_id)
    token_manager.set_credits(token_id, credits)
    return {"token_id": token_id, "credits": credits}


# ---------------- 请求模型 ----------------

class ConfigPatch(BaseModel):
    api_key: Optional[str] = None
    admin_username: Optional[str] = None
    admin_password: Optional[str] = None
    use_proxy: Optional[bool] = None
    proxy: Optional[str] = None
    use_socks5_proxy: Optional[bool] = None
    socks5_proxy: Optional[str] = None
    use_socks5_proxy_chain: Optional[bool] = None
    proxy_mode: Optional[str] = None
    gpt_image_quality: Optional[str] = None
    generate_timeout: Optional[int] = None
    token_rotation_strategy: Optional[str] = None
    external_base_url: Optional[str] = None
    external_api_key: Optional[str] = None
    default_channel: Optional[str] = None


class ImageGenRequest(BaseModel):
    prompt: str
    model_id: str = "firefly-nano-banana-pro-2k-16x9"
    quality: str = "medium"
    seed: Optional[int] = None
    init_image_id: Optional[str] = None


class VideoGenRequest(BaseModel):
    prompt: str
    model_id: str = "firefly-sora2-8s-16x9-720p"
    init_image_id: Optional[str] = None


class TokenAddRequest(BaseModel):
    value: str
    name: Optional[str] = None


class CookieImportRequest(BaseModel):
    cookie: Any
    name: Optional[str] = None


# ---------------- 基础 API ----------------

@app.get("/api/health")
def health():
    return {"ok": True, "ts": int(time.time())}


@app.get("/api/models")
def list_models():
    images = [
        {"id": mid, "type": "image", "family": m["family"],
         "resolution": m["resolution"], "ratio": m["ratio"],
         "description": f'{m["family"]} ({m["resolution"]} {m["ratio"]})'}
        for mid, m in IMAGE_MODELS.items()
    ]
    videos = [
        {"id": mid, "type": "video", "engine": m["engine"],
         "duration": m["duration"], "ratio": m["ratio"], "resolution": m["resolution"],
         "description": f'{m["engine"]} ({m["duration"]}s {m["ratio"]} {m["resolution"]})'}
        for mid, m in VIDEO_MODELS.items()
    ]
    return {"images": images, "videos": videos}


@app.get("/api/config")
def get_config():
    return _public_config(config_manager.get_all())


@app.get("/api/config/proxy-traffic")
def get_proxy_traffic():
    """Traffic observed by the local HTTP -> SOCKS5 relay in this backend run."""
    return get_proxy_chain_traffic()


@app.put("/api/config")
def update_config(patch: ConfigPatch):
    values = patch.model_dump(exclude_none=True)
    if "proxy_mode" in values:
        try:
            values.update(proxy_mode_settings(values.pop("proxy_mode")))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if "socks5_proxy" in values:
        try:
            values["socks5_proxy"] = validate_socks5_proxy(values["socks5_proxy"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    socks5_enabled = bool(values.get("use_socks5_proxy", config_manager.get("use_socks5_proxy")))
    socks5_url = str(values.get("socks5_proxy", config_manager.get("socks5_proxy")) or "").strip()
    if socks5_enabled:
        if not socks5_url:
            raise HTTPException(status_code=400, detail="启用独立 SOCKS5 代理时必须填写代理地址")
        try:
            validate_socks5_proxy(socks5_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    chain_enabled = bool(values.get("use_socks5_proxy_chain", config_manager.get("use_socks5_proxy_chain")))
    if chain_enabled:
        local_enabled = bool(values.get("use_proxy", config_manager.get("use_proxy")))
        local_url = str(values.get("proxy", config_manager.get("proxy")) or "").strip()
        if not local_enabled or not local_url:
            raise HTTPException(status_code=400, detail="启用链式代理时必须启用并填写本地 HTTP 代理")
        if not socks5_url:
            raise HTTPException(status_code=400, detail="启用链式代理时必须填写 SOCKS5 上游代理")
        try:
            validate_socks5_proxy(socks5_url)
            validate_http_connect_proxy(local_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    cfg = update_config_and_invalidate_proxy_chain(values)
    return _public_config(cfg)


@app.post("/api/config/test-channel")
def test_channel():
    """测试服务通道连通性：Firefly（内置）与外部 OpenAI 兼容 API。"""
    import time as _time

    proxies = adobe_client._proxies()
    result: dict[str, Any] = {"tested_at": int(time.time())}

    # ---- Firefly 通道 ----
    firefly: dict[str, Any] = {"ok": False, "message": ""}
    tokens = token_manager.all()
    active = [t for t in tokens if t.get("status") == "active"]
    firefly["token_pool"] = {"total": len(tokens), "active": len(active)}

    t0 = _time.time()
    try:
        r = requests.get(
            "https://firefly-3p.ff.adobe.io/",
            headers={"User-Agent": adobe_client.user_agent},
            timeout=15, proxies=proxies,
        )
        firefly["network"] = {"ok": True, "info": f"HTTP {r.status_code}"}
    except Exception as exc:
        firefly["network"] = {"ok": False, "info": f"{type(exc).__name__}: {exc}"}
    firefly["latency_ms"] = int((_time.time() - t0) * 1000)

    # token 有效性（用第一个 active token 调 Adobe IMS userinfo 校验）
    token_valid: Optional[bool] = None
    user_info = ""
    if active:
        token = str(active[0].get("value") or "")
        try:
            r = requests.get(
                "https://ims-na1.adobelogin.com/ims/userinfo/v3",
                headers={"Authorization": f"Bearer {token}",
                         "User-Agent": adobe_client.user_agent},
                timeout=15, proxies=proxies,
            )
            if r.status_code == 200:
                info = r.json()
                token_valid = True
                user_info = str(info.get("email") or info.get("name") or info.get("sub") or "")
            else:
                token_valid = False
                user_info = f"HTTP {r.status_code}"
        except Exception as exc:
            token_valid = None
            user_info = f"{type(exc).__name__}: {exc}"
    firefly["token"] = {"valid": token_valid, "info": user_info}

    if not firefly["network"]["ok"]:
        firefly["message"] = "网络不通，无法访问 firefly-3p.ff.adobe.io"
    elif token_valid is False:
        firefly["message"] = "Firefly 域名可达，但 token 无效（401/403）"
    elif token_valid is None:
        firefly["message"] = "Firefly 域名可达；未配置 token 或校验未执行"
    else:
        firefly["ok"] = True
        firefly["message"] = f"Firefly 通道正常，token 有效（{user_info}）"
    if firefly["token_pool"]["total"] == 0:
        firefly["message"] += "；尚未导入 cookie/token"
    result["firefly"] = firefly

    # ---- 外部 OpenAI 兼容通道 ----
    external: dict[str, Any] = {"ok": False, "message": ""}
    base_url = str(config_manager.get("external_base_url") or "").strip()
    api_key = str(config_manager.get("external_api_key") or "").strip()
    if base_url:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        t0 = _time.time()
        try:
            # 本地地址不走代理，避免循环或错误转发
            is_local = _is_local_url(base_url)
            r = requests.get(base_url.rstrip("/") + "/models",
                             headers=headers, timeout=20,
                             proxies=(None if is_local else proxies))
            external["latency_ms"] = int((_time.time() - t0) * 1000)
            external["http_code"] = r.status_code
            if r.status_code == 200:
                data = r.json()
                models = data.get("data", []) if isinstance(data, dict) else []
                external["model_count"] = len(models) if isinstance(models, list) else 0
                external["ok"] = True
                external["message"] = f"外部通道正常，返回 {external['model_count']} 个模型"
            else:
                external["message"] = f"HTTP {r.status_code}：{str(r.text)[:150]}"
        except Exception as exc:
            external["message"] = f"{type(exc).__name__}: {exc}"
    else:
        external["message"] = "未配置外部 API（Base URL 为空），此通道未启用"
    result["external"] = external

    # ---- 代理 ----
    def _test_proxy(kind: str) -> dict:
        try:
            proxy_map = get_requests_proxies(kind)
        except Exception as exc:
            return {"ok": False, "info": f"{type(exc).__name__}: {exc}"}
        if not proxy_map:
            return {"ok": None, "info": "未启用"}
        t0 = _time.time()
        try:
            r = requests.get("https://firefly-3p.ff.adobe.io/", timeout=15, proxies=proxy_map)
            return {
                "ok": r.status_code < 500,
                "latency_ms": int((_time.time() - t0) * 1000),
                "info": f"HTTP {r.status_code}",
            }
        except Exception as exc:
            return {"ok": False, "info": f"{type(exc).__name__}: {exc}"}

    active_proxy_kind, _ = get_active_proxy()
    result["local_proxy"] = _test_proxy("local")
    if active_proxy_kind == "chain":
        result["socks5_proxy"] = {
            "ok": None,
            "info": "链式代理已启用，已跳过不经过本地 HTTP 代理的 SOCKS5 直连测试",
        }
    else:
        result["socks5_proxy"] = _test_proxy("socks5")
    result["proxy_chain"] = _test_proxy("chain")
    if active_proxy_kind == "chain":
        result["proxy"] = result["proxy_chain"]
    elif active_proxy_kind == "socks5":
        result["proxy"] = result["socks5_proxy"]
    elif active_proxy_kind == "local":
        result["proxy"] = result["local_proxy"]
    else:
        result["proxy"] = {"ok": None, "info": "未启用"}

    log_store.add(
        method="GET", path="/api/test_channel",
        status=200 if (firefly.get("ok") or external.get("ok")) else 500,
        duration_ms=firefly.get("latency_ms", 0) + external.get("latency_ms", 0),
    )

    return result


# ---------------- Token / Cookie ----------------

@app.get("/api/tokens")
def list_tokens():
    return [_public_token(entry) for entry in token_manager.all()]


@app.post("/api/tokens")
def add_token(req: TokenAddRequest):
    try:
        entry = token_manager.add(req.value, name=req.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _public_token(entry)


@app.post("/api/tokens/import-batch")
def import_tokens_batch(body: dict):
    """批量导入 token。支持：多行文本 / JSON 数组 / {"tokens": [...]}。

    每个 token 可直接是字符串，或对象 {"value": "...", "name": "..."}。
    """
    def _parse(raw) -> list[dict]:
        if isinstance(raw, dict) and "tokens" in raw:
            return _parse(raw["tokens"])
        if isinstance(raw, str):
            text = raw.strip()
            if text[:1] in ("[", "{"):
                try:
                    return _parse(json.loads(text))
                except Exception:
                    pass
            return [{"value": ln.strip(), "name": None} for ln in text.splitlines() if ln.strip()]
        if isinstance(raw, list):
            out = []
            for item in raw:
                if isinstance(item, str):
                    if item.strip():
                        out.append({"value": item.strip(), "name": None})
                elif isinstance(item, dict):
                    v = item.get("value") or item.get("token")
                    if v and str(v).strip():
                        out.append({"value": str(v).strip(), "name": item.get("name")})
            return out
        return []

    items = _parse(body.get("tokens"))
    if not items:
        raise HTTPException(status_code=400, detail="未解析到有效的 token，请检查输入格式")

    imported: list[dict] = []
    failed: list[dict] = []
    for idx, item in enumerate(items):
        try:
            entry = token_manager.add(item["value"], name=item.get("name"))
            imported.append(entry)
        except ValueError as exc:
            failed.append({"index": idx, "error": str(exc)})

    return {"total": len(items), "ok": [_public_token(entry) for entry in imported], "failed": failed}


@app.delete("/api/tokens/{token_id}")
def delete_token(token_id: str):
    token_manager.remove(token_id)
    return {"ok": True}


@app.post("/api/tokens/{token_id}/status")
def set_token_status(token_id: str, body: dict):
    status = str(body.get("status") or "")
    if status not in ("valid", "invalid"):
        raise HTTPException(status_code=400, detail="status must be valid|invalid")
    token_manager.update_status(token_id, status)
    return {"ok": True}


@app.post("/api/cookies/import")
def import_cookie(req: CookieImportRequest):
    try:
        result = refresh_manager.import_cookie(req.cookie, name=req.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _public_refresh_result(result)


@app.post("/api/cookies/import-batch")
def import_cookies_batch(body: dict):
    """批量导入 cookie：一个文件 / 文本里包含多个 cookie 时自动识别并逐个导入刷新。"""
    raw = body.get("cookies")
    if raw is None:
        raise HTTPException(status_code=400, detail="缺少 cookies 字段")
    try:
        result = refresh_manager.import_cookies_batch(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "total": result["total"],
        "ok": [_public_refresh_result(item) for item in result["ok"]],
        "failed": result["failed"],
    }


@app.get("/api/cookies")
def list_cookies():
    return refresh_manager.list_profiles()


@app.delete("/api/cookies/{profile_id}")
def delete_cookie(profile_id: str):
    refresh_manager.delete_profile(profile_id)
    return {"ok": True}


@app.post("/api/cookies/delete-batch")
def delete_cookies_batch(body: dict):
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids must be a list")
    deleted = refresh_manager.delete_profiles_batch([str(i) for i in ids])
    return {"ok": True, "deleted": deleted}


@app.post("/api/cookies/{profile_id}/refresh")
def refresh_cookie(profile_id: str):
    try:
        return _public_refresh_result(refresh_manager.refresh_profile(profile_id))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------- 扩展 Token API（兼容 adobe2api 管理界面） ----------

@app.post("/api/tokens/{token_id}/credits/refresh")
def refresh_token_credits(token_id: str):
    try:
        return _refresh_credits_for_token_id(token_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="token not found")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/tokens/credits/refresh-batch")
def refresh_token_credits_batch(body: dict):
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids must be a list")
    results = []
    for tid in ids:
        try:
            results.append(_refresh_credits_for_token_id(tid))
        except Exception as exc:
            results.append({"token_id": tid, "error": str(exc)})
    return {"results": results}


@app.get("/api/tokens/credits/total")
def total_credits():
    return token_manager.total_credits()


@app.post("/api/tokens/delete-batch")
def delete_tokens_batch(body: dict):
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids must be a list")
    token_manager.remove_batch(ids)
    return {"ok": True, "deleted": len(ids)}


@app.post("/api/tokens/export")
def export_tokens():
    tokens = token_manager.all()
    export_data = [{"id": t["id"], "value": t.get("value", ""), "name": t.get("name", ""),
                    "status": t.get("status", ""), "fails": t.get("fails", 0),
                    "added_at": t.get("added_at", 0)} for t in tokens]
    return {"tokens": export_data}


@app.post("/api/tokens/export-cookies")
def export_cookies():
    profiles = refresh_manager.list_profiles()
    export_data = [{"id": p["id"], "name": p.get("name", ""), "cookie": p.get("cookie", ""),
                    "added_at": p.get("added_at", 0)} for p in profiles]
    return {"cookies": export_data}


@app.post("/api/tokens/{token_id}/auto-refresh")
def set_token_auto_refresh(token_id: str, body: dict):
    enabled = bool(body.get("enabled", True))
    token_manager.update_auto_refresh(token_id, enabled)
    return {"ok": True, "token_id": token_id, "auto_refresh": enabled}


# ---------- 日志 API ----------

@app.get("/api/logs")
def list_logs(page: int = 1, per_page: int = 20, status: str = None, model: str = None):
    return log_store.list_logs(page=page, per_page=per_page, status=status, model=model)


@app.get("/api/logs/stats")
def logs_stats():
    return log_store.stats()


@app.get("/api/logs/running")
def logs_running():
    return log_store.running_tasks()


@app.get("/api/logs/{log_id}")
def get_log_detail(log_id: str):
    log = log_store.get_by_id(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="log not found")
    return log


# ---------------- 生成 API ----------------

@app.post("/api/generate/image")
def generate_image(req: ImageGenRequest):
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt 不能为空")
    t0 = time.time()
    task_id = start_generation("image", req.model_dump())
    log_store.add(
        method="POST", path="/api/generate/image", model=req.model_id,
        status=200, duration_ms=int((time.time() - t0) * 1000),
    )
    return {"task_id": task_id}


@app.post("/api/generate/video")
def generate_video(req: VideoGenRequest):
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt 不能为空")
    t0 = time.time()
    task_id = start_generation("video", req.model_dump())
    log_store.add(
        method="POST", path="/api/generate/video", model=req.model_id,
        status=200, duration_ms=int((time.time() - t0) * 1000),
    )
    return {"task_id": task_id}


@app.post("/api/generate/upload")
async def upload_media(file: UploadFile = File(...)):
    """上传图片/视频素材，返回素材 id（用于图生图/图生视频）。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="no file")
    is_video = str(file.filename).lower().endswith((".mp4", ".webm", ".mov", ".mkv"))
    suffix = Path(file.filename).suffix or (".mp4" if is_video else ".png")
    save_dir = OUTPUTS_DIR / "uploads"
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / f"{uuid.uuid4().hex[:12]}{suffix}"
    save_path.write_bytes(await file.read())

    try:
        token = adobe_client._require_token()
        media_id = adobe_client.upload_file(save_path, is_video=is_video, token=token["value"])
    except AdobeRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc.user_message or exc))
    return {
        "media_id": media_id,
        "kind": "video" if is_video else "image",
        "local_url": f"/outputs/uploads/{save_path.name}",
    }


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return {
        "id": task["id"], "kind": task["kind"], "status": task["status"],
        "progress": task["progress"], "message": task["message"],
        "error": task["error"], "assets": task["assets"],
    }


# ---------------- OpenAI 兼容网关 ----------------

def _external_models() -> list[str]:
    base_url = str(config_manager.get("external_base_url") or "").strip()
    if not base_url:
        return []
    try:
        api_key = str(config_manager.get("external_api_key") or "").strip()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # 本地地址不走代理
        is_local = _is_local_url(base_url)
        proxies = adobe_client._proxies() if not is_local else None
        resp = requests.get(base_url.rstrip("/") + "/models",
                            headers=headers, timeout=30, proxies=proxies)
        if resp.status_code == 200:
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
    except Exception:
        pass
    return []


@app.get("/v1/models")
def openai_models():
    data = []
    for mid, m in IMAGE_MODELS.items():
        data.append({"id": mid, "object": "model",
                     "owned_by": "firefly", "type": "image",
                     "description": f'{m["family"]} ({m["resolution"]} {m["ratio"]})'})
    for mid, m in VIDEO_MODELS.items():
        data.append({"id": mid, "object": "model",
                     "owned_by": "firefly", "type": "video",
                     "description": f'{m["engine"]} ({m["duration"]}s {m["ratio"]} {m["resolution"]})'})
    return {"object": "list", "data": data}


@app.post("/v1/images/generations")
def openai_image_gen(body: dict):
    model = str(body.get("model") or "firefly-nano-banana-pro-2k-16x9")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    init_id = body.get("init_image_id") or body.get("image_id")

    if model not in IMAGE_MODELS:
        # 尝试转发外部通道
        t0 = time.time()
        external = _forward_external("/images/generations", body)
        if external is not None:
            log_store.add(
                method="POST", path="/v1/images/generations", model=model,
                status=200, duration_ms=int((time.time() - t0) * 1000),
            )
            return external
        log_store.add(
            method="POST", path="/v1/images/generations", model=model,
            status=400, error="unknown model and no external api configured",
            duration_ms=int((time.time() - t0) * 1000),
        )
        raise HTTPException(status_code=400, detail=f"unknown model: {model}")

    t0 = time.time()
    task_id = start_generation("image", {
        "prompt": prompt, "model_id": model,
        "quality": str(body.get("quality") or config_manager.get("gpt_image_quality", "medium")),
        "seed": body.get("seed"),
        "init_image_id": init_id,
    })
    log_store.add(
        method="POST", path="/v1/images/generations", model=model,
        status=200, duration_ms=int((time.time() - t0) * 1000),
    )
    return _openai_task_response(task_id, body)


@app.post("/v1/videos/generations")
def openai_video_gen(body: dict):
    model = str(body.get("model") or "firefly-sora2-8s-16x9-720p")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    if model not in VIDEO_MODELS:
        raise HTTPException(status_code=400, detail=f"unknown model: {model}")
    t0 = time.time()
    task_id = start_generation("video", {
        "prompt": prompt, "model_id": model,
        "init_image_id": body.get("init_image_id") or body.get("image_id"),
    })
    log_store.add(
        method="POST", path="/v1/videos/generations", model=model,
        status=200, duration_ms=int((time.time() - t0) * 1000),
    )
    return _openai_task_response(task_id, body)


def _openai_task_response(task_id: str, body: dict) -> JSONResponse:
    async_mode = bool(body.get("async") or body.get("wait") is False)
    if async_mode:
        return JSONResponse({"task_id": task_id, "status": "queued"})
    # 同步等待：轮询任务直到完成（timeout 由 generate_timeout 决定）
    timeout = int(config_manager.get("generate_timeout", 300)) + 30
    start = time.time()
    while time.time() - start < timeout:
        with TASKS_LOCK:
            task = dict(TASKS.get(task_id, {}))
        if not task:
            break
        if task["status"] == "done":
            data = []
            for a in task["assets"]:
                if a["kind"] == "video":
                    data.append({"url": a["url"], "type": "video/mp4"})
                else:
                    data.append({"url": a["url"], "type": "image/png"})
            return JSONResponse({"created": int(time.time()), "data": data,
                                 "task_id": task_id})
        if task["status"] == "failed":
            raise HTTPException(status_code=500, detail=task["error"] or "generation failed")
        time.sleep(1)
    raise HTTPException(status_code=504, detail="generation timeout")


def _forward_external(path: str, body: dict):
    base_url = str(config_manager.get("external_base_url") or "").strip()
    if not base_url:
        return None
    t0 = time.time()
    error = None
    status = 200
    try:
        import requests as _req

        api_key = str(config_manager.get("external_api_key") or "").strip()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # 本地地址不走代理
        is_local = _is_local_url(base_url)
        proxies = adobe_client._proxies() if not is_local else None
        resp = _req.post(base_url.rstrip("/") + path, json=body,
                         headers=headers, timeout=int(config_manager.get("generate_timeout", 300)),
                         proxies=proxies)
        status = resp.status_code
        if resp.status_code < 400:
            log_store.add(
                method="POST", path=path, model=body.get("model"),
                status=status, duration_ms=int((time.time() - t0) * 1000),
            )
            return resp.json()
    except Exception as exc:
        error = str(exc)
    log_store.add(
        method="POST", path=path, model=body.get("model"),
        status=status, error=error, duration_ms=int((time.time() - t0) * 1000),
    )
    return None


# ================= OpenAI 兼容补充端点（infinite-canvas 前端） =================

def _external_base():
    return str(config_manager.get("external_base_url") or "").strip()


def _external_headers():
    headers = {"Content-Type": "application/json"}
    api_key = str(config_manager.get("external_api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _http_proxies(url: str):
    if _is_local_url(url):
        return None
    return adobe_client._proxies()


def _to_openai_messages(input_items) -> list:
    """Responses API input 数组 -> OpenAI chat messages。"""
    messages: list[dict] = []
    for item in input_items:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "function_call":
            continue
        if itype == "function_call_output":
            messages.append({
                "role": "tool",
                "tool_call_id": str(item.get("call_id") or ""),
                "content": str(item.get("output") or ""),
            })
            continue
        role = str(item.get("role") or "user")
        if role not in ("system", "assistant", "user", "tool"):
            role = "user"
        content = item.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype in ("input_text", "text"):
                    parts.append({"type": "text", "text": str(part.get("text") or "")})
                elif ptype == "input_image":
                    image_url = part.get("image_url")
                    url = image_url.get("url") if isinstance(image_url, dict) else None
                    if url:
                        parts.append({"type": "image_url", "image_url": {"url": url}})
            content = parts
        messages.append({"role": role, "content": content})
    if not messages:
        raise HTTPException(status_code=400, detail="input is required")
    return messages


def _responses_payload(text: str, tool_calls: list, model: str) -> dict:
    output = []
    for call in tool_calls or []:
        fn = call.get("function") or {}
        output.append({
            "type": "function_call",
            "id": call.get("id"),
            "call_id": call.get("id"),
            "name": str(fn.get("name") or ""),
            "arguments": str(fn.get("arguments") or "{}"),
        })
    if text:
        output.append({
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
            "annotations": [],
        })
    return {
        "id": "resp_" + uuid.uuid4().hex[:24],
        "object": "response",
        "created_at": int(time.time()),
        "model": model,
        "status": "completed",
        "output": output,
        "output_text": text,
    }


def _chat_completion_text(data: dict) -> str:
    try:
        message = (data.get("choices") or [{}])[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
        return str(content)
    except Exception:
        return ""


@app.get("/models")
def openai_models_alias():
    return openai_models()


@app.post("/responses")
def openai_responses(body: dict):
    """OpenAI Responses API 兼容：文本对话/工具调用（转发外部渠道，SSE 流式）。"""
    model = str(body.get("model") or config_manager.get("chat_model", "gpt-4o-mini"))
    stream = body.get("stream") is True
    messages = _to_openai_messages(body.get("input") or [])

    if model in IMAGE_MODELS or model in VIDEO_MODELS:
        raise HTTPException(status_code=400, detail=f"本地生成模型不支持文本对话: {model}")

    tools = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function":
            fn = {k: tool.get(k) for k in ("name", "description", "parameters") if tool.get(k) is not None}
            tools.append({"type": "function", "function": fn})
    chat_body: dict = {"model": model, "messages": messages, "stream": stream}
    for k in ("temperature", "max_tokens", "top_p", "tool_choice"):
        if k in body:
            chat_body[k] = body[k]
    if tools:
        chat_body["tools"] = tools

    base_url = _external_base()
    if not base_url:
        raise HTTPException(status_code=502, detail="未配置外部 API（文本对话需要外部渠道）")

    import requests as _req
    endpoint = base_url.rstrip("/") + "/chat/completions"
    proxies = _http_proxies(base_url)
    timeout = int(config_manager.get("generate_timeout", 300)) + 30

    if stream:
        def gen():
            try:
                resp = _req.post(endpoint, json=chat_body, headers=_external_headers(),
                                 stream=True, timeout=timeout, proxies=proxies)
            except Exception as exc:
                yield "data: " + json.dumps({"type": "error", "error": {"message": f"外部渠道请求失败: {exc}"}}) + "\n\n"
                return
            if resp.status_code >= 400:
                yield "data: " + json.dumps({"type": "error", "error": {"message": resp.text[:300]}}) + "\n\n"
                return
            tool_calls: list[dict] = []
            pending: dict[int, dict] = {}
            text_parts: list[str] = []
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    evt = json.loads(payload)
                except Exception:
                    continue
                choices = evt.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    text_parts.append(text)
                    yield "data: " + json.dumps({"type": "response.output_text.delta", "delta": text}) + "\n\n"
                for tc in delta.get("tool_calls") or []:
                    idx = int(tc.get("index") or 0)
                    slot = pending.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    if tc.get("function", {}).get("name"):
                        slot["function"]["name"] = tc["function"]["name"]
                    if tc.get("function", {}).get("arguments"):
                        slot["function"]["arguments"] += tc["function"]["arguments"]
            tool_calls = list(pending.values())
            text_out = "".join(text_parts)
            yield "data: " + json.dumps({"type": "response.completed",
                                         "response": _responses_payload(text_out, tool_calls, model)}) + "\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    try:
        resp = _req.post(endpoint, json=chat_body, headers=_external_headers(),
                         timeout=timeout, proxies=proxies)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"外部渠道请求失败: {exc}")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=resp.text[:300])
    data = resp.json()
    text = _chat_completion_text(data)
    tool_calls = ((data.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
    return _responses_payload(text, tool_calls, model)


@app.post("/images/edits")
async def openai_image_edits(
    model: str = Form("gpt-image-2"),
    prompt: str = Form(""),
    n: int = Form(1),
    quality: str = Form(None),
    size: str = Form(None),
    background: str = Form(None),
    image: list[UploadFile] = File(default=[]),
    mask: UploadFile = File(None),
):
    """OpenAI images/edits 兼容：图生图（multipart form-data）。"""
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    if not image:
        raise HTTPException(status_code=400, detail="image is required")
    first = image[0]
    suffix = Path(first.filename or "edit.png").suffix or ".png"
    save_dir = OUTPUTS_DIR / "uploads"
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / f"{uuid.uuid4().hex[:12]}{suffix}"
    save_path.write_bytes(await first.read())
    t0 = time.time()

    if model in IMAGE_MODELS:
        try:
            token = adobe_client._require_token()
            media_id = adobe_client.upload_file(save_path, is_video=False, token=token["value"])
        except (AdobeRequestError, AuthError) as exc:
            raise HTTPException(status_code=400, detail=str(getattr(exc, "user_message", None) or exc))
        task_id = start_generation("image", {
            "prompt": prompt, "model_id": model,
            "quality": str(quality or config_manager.get("gpt_image_quality", "medium")),
            "init_image_id": media_id,
        })
        log_store.add(method="POST", path="/images/edits", model=model,
                      status=200, duration_ms=int((time.time() - t0) * 1000))
        return _openai_task_response(task_id, {"model": model})

    # 非本地模型：转发外部 OpenAI 兼容 multipart 接口
    base_url = _external_base()
    if not base_url:
        raise HTTPException(status_code=502, detail=f"未知模型且未配置外部 API: {model}")
    import requests as _req
    files = {"image": (first.filename or "image.png", save_path.open("rb"),
                       first.content_type or "image/png")}
    form_data = {"model": model, "prompt": prompt, "n": str(n)}
    if quality:
        form_data["quality"] = quality
    if size:
        form_data["size"] = size
    if background:
        form_data["background"] = background
    if mask:
        mask_suffix = Path(mask.filename or "mask.png").suffix or ".png"
        mask_path = save_dir / f"{uuid.uuid4().hex[:12]}{mask_suffix}"
        mask_path.write_bytes(await mask.read())
        files["mask"] = (mask.filename or "mask.png", mask_path.open("rb"),
                         mask.content_type or "image/png")
    try:
        resp = _req.post(base_url.rstrip("/") + "/images/edits", data=form_data, files=files,
                         headers={"Authorization": _external_headers().get("Authorization") or ""},
                         timeout=int(config_manager.get("generate_timeout", 300)) + 30,
                         proxies=_http_proxies(base_url))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"外部渠道请求失败: {exc}")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=resp.text[:300])
    log_store.add(method="POST", path="/images/edits", model=model,
                  status=200, duration_ms=int((time.time() - t0) * 1000))
    return JSONResponse(resp.json())


@app.post("/audio/speech")
def openai_audio_speech(body: dict):
    """OpenAI audio/speech 兼容：TTS（先转发外部渠道，失败则用本地 edge-tts）。"""
    text = str(body.get("input") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="input is required")
    model = str(body.get("model") or "tts-1")
    voice = str(body.get("voice") or "alloy")
    response_format = str(body.get("response_format") or "mp3")
    try:
        speed = float(body.get("speed") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0

    base_url = _external_base()
    if base_url:
        import requests as _req
        try:
            resp = _req.post(base_url.rstrip("/") + "/audio/speech",
                             json={"model": model, "input": text, "voice": voice,
                                   "response_format": response_format, "speed": speed},
                             headers=_external_headers(),
                             timeout=int(config_manager.get("generate_timeout", 300)) + 30,
                             proxies=_http_proxies(base_url))
            if resp.status_code < 400:
                ctype = resp.headers.get("Content-Type") or "audio/mpeg"
                return Response(content=resp.content, media_type=ctype)
        except Exception as exc:
            logger.warning("audio/speech external failed: %s", exc)

    # 本地 edge-tts 兜底（免费微软 TTS）
    try:
        import edge_tts
    except ImportError:
        raise HTTPException(status_code=501, detail="未配置外部 TTS 渠道，且后端未安装 edge-tts")
    voice_map = {
        "alloy": "zh-CN-XiaoxiaoNeural",
        "echo": "zh-CN-YunxiNeural",
        "fable": "zh-CN-XiaoyiNeural",
        "onyx": "zh-CN-YunjianNeural",
        "nova": "zh-CN-XiaoyiNeural",
        "shimmer": "zh-CN-XiaoxiaoNeural",
        "zh-CN-XiaoxiaoNeural": "zh-CN-XiaoxiaoNeural",
        "zh-CN-YunxiNeural": "zh-CN-YunxiNeural",
        "zh-CN-XiaoyiNeural": "zh-CN-XiaoyiNeural",
        "zh-CN-YunjianNeural": "zh-CN-YunjianNeural",
        "en-US-AriaNeural": "en-US-AriaNeural",
        "en-US-GuyNeural": "en-US-GuyNeural",
    }
    selected = voice_map.get(voice, voice)
    rate = f"{int(round((speed - 1.0) * 100)):+d}%"
    proxy_kind, proxy_url = get_active_proxy()
    socks_connector_cls = None
    socks_url = ""
    remote_dns = False
    if proxy_url:
        if proxy_kind in {"socks5", "chain"}:
            try:
                from aiohttp_socks import ProxyConnector
            except ImportError:
                raise HTTPException(status_code=501, detail="SOCKS5 代理需要安装 aiohttp-socks")
            socks_connector_cls = ProxyConnector
            socks_url = proxy_url
            remote_dns = socks_url.lower().startswith("socks5h://")
            if remote_dns:
                socks_url = "socks5://" + socks_url[len("socks5h://"):]

    async def _synth():
        edge_tts_options: dict[str, Any] = {}
        if socks_connector_cls:
            edge_tts_options["connector"] = socks_connector_cls.from_url(socks_url, rdns=remote_dns)
        elif proxy_url:
            edge_tts_options["proxy"] = proxy_url
        communicate = edge_tts.Communicate(text, selected, rate=rate, **edge_tts_options)
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    try:
        import asyncio
        audio = asyncio.run(_synth())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TTS 生成失败: {exc}")
    return Response(content=audio, media_type="audio/mpeg")


@app.post("/videos")
async def openai_videos_create(
    model: str = Form("firefly-sora2-8s-16x9-720p"),
    prompt: str = Form(""),
    seconds: str = Form("8"),
    size: str = Form(None),
    resolution_name: str = Form("16:9 (1920x1080)"),
    preset: str = Form("normal"),
    input_reference: list[UploadFile] = File(default=[]),
):
    """Veo 风格视频任务创建（multipart form-data），返回任务 id。"""
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    init_image_id = None
    if input_reference:
        ref = input_reference[0]
        suffix = Path(ref.filename or "ref.png").suffix or ".png"
        save_dir = OUTPUTS_DIR / "uploads"
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / f"{uuid.uuid4().hex[:12]}{suffix}"
        save_path.write_bytes(await ref.read())
        try:
            token = adobe_client._require_token()
            init_image_id = adobe_client.upload_file(save_path, is_video=False, token=token["value"])
        except (AdobeRequestError, AuthError) as exc:
            raise HTTPException(status_code=400, detail=str(getattr(exc, "user_message", None) or exc))
    if model not in VIDEO_MODELS:
        raise HTTPException(status_code=400, detail=f"unknown model: {model}")
    t0 = time.time()
    task_id = start_generation("video", {
        "prompt": prompt, "model_id": model, "init_image_id": init_image_id,
    })
    log_store.add(method="POST", path="/videos", model=model,
                  status=200, duration_ms=int((time.time() - t0) * 1000))
    return {"id": task_id}


@app.get("/videos/{task_id}")
def openai_videos_status(task_id: str):
    with TASKS_LOCK:
        task = dict(TASKS.get(task_id, {}))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task["status"] == "done":
        url = ""
        for a in task.get("assets") or []:
            if a.get("kind") == "video":
                url = a.get("url", "")
                break
        if not url and task.get("assets"):
            url = task["assets"][0].get("url", "")
        return {"id": task_id, "status": "completed", "video_url": url, "url": url}
    if task["status"] == "failed":
        return {"id": task_id, "status": "failed",
                "error": {"message": task.get("error") or "generation failed"}}
    return {"id": task_id, "status": "in_progress",
            "progress": task.get("progress", 0), "message": task.get("message", "")}


@app.get("/videos/{task_id}/content")
def openai_videos_content(task_id: str):
    with TASKS_LOCK:
        task = dict(TASKS.get(task_id, {}))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task["status"] != "done":
        raise HTTPException(status_code=409, detail="task not completed")
    url = ""
    for a in task.get("assets") or []:
        if a.get("kind") == "video":
            url = a.get("url", "")
            break
    if not url and task.get("assets"):
        url = task["assets"][0].get("url", "")
    if not url:
        raise HTTPException(status_code=404, detail="no video asset")
    local = OUTPUTS_DIR / url.replace("/outputs/", "")
    if not local.exists():
        raise HTTPException(status_code=404, detail="video file not found")
    return FileResponse(local, media_type="video/mp4")


def _extract_prompt_from_messages(messages: Any) -> str:
    """从 OpenAI messages 数组中提取文本 prompt（与 adobe2api 一致）。"""
    if not isinstance(messages, list):
        return ""
    parts: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type")) == "text":
                    parts.append(str(item.get("text") or ""))
    return "\n".join(p for p in parts if p and p.strip()).strip()


@app.post("/v1/chat/completions")
def openai_chat(body: dict):
    t0 = time.time()
    prompt = _extract_prompt_from_messages(body.get("messages") or []) or str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="messages or prompt is required")
    model = str(body.get("model") or "firefly-nano-banana-pro-2k-16x9")

    # 本地模型：直接走本地生成（图像/视频），与 adobe2api 的 chat/completions 行为一致
    if model in IMAGE_MODELS:
        task_id = start_generation("image", {
            "prompt": prompt, "model_id": model,
            "quality": str(body.get("quality") or config_manager.get("gpt_image_quality", "medium")),
            "seed": body.get("seed"),
            "init_image_id": body.get("init_image_id") or body.get("image_id"),
        })
        log_store.add(
            method="POST", path="/v1/chat/completions", model=model,
            status=200, duration_ms=int((time.time() - t0) * 1000),
        )
        return _openai_task_response(task_id, body)
    if model in VIDEO_MODELS:
        task_id = start_generation("video", {
            "prompt": prompt, "model_id": model,
            "init_image_id": body.get("init_image_id") or body.get("image_id"),
        })
        log_store.add(
            method="POST", path="/v1/chat/completions", model=model,
            status=200, duration_ms=int((time.time() - t0) * 1000),
        )
        return _openai_task_response(task_id, body)

    # 非本地模型：转发外部 OpenAI 兼容 API
    forwarded = _forward_external("/chat/completions", body)
    if forwarded is not None:
        log_store.add(
            method="POST", path="/v1/chat/completions", model=model,
            status=200, duration_ms=int((time.time() - t0) * 1000),
        )
        return forwarded
    log_store.add(
        method="POST", path="/v1/chat/completions", model=model,
        status=400, error="unknown model and no external api configured",
        duration_ms=int((time.time() - t0) * 1000),
    )
    raise HTTPException(status_code=400, detail=f"未知模型且未配置外部 API: {model}")


@app.post("/v1/completions")
def openai_completions(body: dict):
    forwarded = _forward_external("/completions", body)
    if forwarded is not None:
        return forwarded
    raise HTTPException(status_code=400, detail="未配置外部 OpenAI 兼容 API（external_base_url）")


# ---------------- 项目 / 提示词 / 素材管理 ----------------

from backend.project_store import (  # noqa: E402
    create_project,
    delete_project,
    get_canvas,
    get_project,
    list_projects,
    rename_project,
    save_canvas,
)
from backend.prompt_store import (  # noqa: E402
    add_prompt,
    delete_prompt,
    list_prompts,
    update_prompt,
)


class ProjectCreateRequest(BaseModel):
    name: str = ""


class ProjectCanvasRequest(BaseModel):
    version: int = 1
    nodes: list = []
    edges: list = []
    viewport: dict = {"x": 0, "y": 0, "scale": 1}


class PromptCreateRequest(BaseModel):
    title: str = ""
    content: str = ""
    category: str = ""
    tags: list = []


class PromptUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list] = None


@app.get("/api/projects")
def api_projects_list():
    return list_projects()


@app.post("/api/projects")
def api_projects_create(req: ProjectCreateRequest):
    return create_project(req.name)


@app.put("/api/projects/{project_id}")
def api_projects_rename(project_id: str, req: ProjectCreateRequest):
    project = rename_project(project_id, req.name)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


@app.delete("/api/projects/{project_id}")
def api_projects_delete(project_id: str):
    if not delete_project(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"ok": True}


@app.get("/api/projects/{project_id}/canvas")
def api_projects_canvas_get(project_id: str):
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    return get_canvas(project_id)


@app.put("/api/projects/{project_id}/canvas")
def api_projects_canvas_save(project_id: str, req: ProjectCanvasRequest):
    if not get_project(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    save_canvas(project_id, req.model_dump())
    return {"ok": True}


@app.get("/api/prompts")
def api_prompts_list(category: str = ""):
    return list_prompts(category)


@app.post("/api/prompts")
def api_prompts_add(req: PromptCreateRequest):
    return add_prompt(req.title, req.content, req.category, req.tags)


@app.put("/api/prompts/{prompt_id}")
def api_prompts_update(prompt_id: str, req: PromptUpdateRequest):
    item = update_prompt(prompt_id, req.model_dump(exclude_none=True))
    if not item:
        raise HTTPException(status_code=404, detail="提示词不存在")
    return item


@app.delete("/api/prompts/{prompt_id}")
def api_prompts_delete(prompt_id: str):
    if not delete_prompt(prompt_id):
        raise HTTPException(status_code=404, detail="提示词不存在")
    return {"ok": True}


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}


@app.get("/api/assets")
def api_assets_list():
    """列出 outputs 目录下所有图片/视频素材（含 uploads 与生成结果）。"""
    assets = []
    if OUTPUTS_DIR.exists():
        for p in OUTPUTS_DIR.rglob("*"):
            if not p.is_file():
                continue
            suffix = p.suffix.lower()
            if suffix in IMAGE_EXTS:
                kind = "image"
            elif suffix in VIDEO_EXTS:
                kind = "video"
            else:
                continue
            try:
                size = p.stat().st_size
                mtime = int(p.stat().st_mtime)
            except Exception:
                size, mtime = 0, 0
            assets.append({
                "url": f"/outputs/{p.relative_to(OUTPUTS_DIR).as_posix()}",
                "name": p.name,
                "kind": kind,
                "size": size,
                "mtime": mtime,
            })
    assets.sort(key=lambda a: a["mtime"], reverse=True)
    return assets


@app.post("/api/assets/save")
async def api_assets_save(file: UploadFile = File(...)):
    """保存编辑器导出的图片到 outputs/uploads（仅本地，不调用 Adobe）。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="no file")
    suffix = Path(file.filename).suffix.lower() or ".png"
    if suffix not in IMAGE_EXTS and suffix not in VIDEO_EXTS:
        suffix = ".png"
    save_dir = OUTPUTS_DIR / "uploads"
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / f"{uuid.uuid4().hex[:12]}{suffix}"
    save_path.write_bytes(await file.read())
    kind = "video" if suffix in VIDEO_EXTS else "image"
    return {
        "url": f"/outputs/uploads/{save_path.name}",
        "name": save_path.name,
        "kind": kind,
    }


# ---------------- 静态资源 ----------------

if WEB_DIST.exists():
    app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
else:
    # 开发模式：outputs 仍需服务（vite dev proxy 会转发）
    app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")


if __name__ == "__main__":
    import uvicorn

    print("=" * 50)
    print("  本地画布 Local Canvas")
    print("  请访问: http://localhost:8900")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8900, log_level="info")
