"""Firefly 3P API 客户端（精简版，机制与 adobe2api 一致）。

实现：
  - 文生图 / 图生图（3p-images/generate-async + 轮询）
  - 文生视频 / 图生视频（3p-videos/generate-async + 轮询）
  - 图片/视频上传（storage/image、storage/video）
  - 请求签名（x-nonce、x-arp-session-id）
  - 结果下载到 outputs/ 目录
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests

from backend.config_manager import config_manager
from backend.token_manager import token_manager

logger = logging.getLogger("local-canvas")

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = BASE_DIR / "outputs"

# ---------------- 签名工具（与 adobe2api 一致） ----------------

def decode_jwt_payload(token: str) -> dict[str, Any]:
    raw = str(token or "").strip()
    if not raw:
        return {}
    parts = raw.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    pad = "=" * (-len(payload) % 4)
    try:
        data = base64.urlsafe_b64decode(payload + pad)
        return json.loads(data)
    except Exception:
        return {}


def build_submit_nonce(token: str, prompt: str) -> str:
    claims = decode_jwt_payload(token)
    user_id = str(
        claims.get("user_id") or claims.get("aa_id") or claims.get("sub") or ""
    ).strip()
    prompt_prefix = str(prompt or "")[:256]
    if not user_id or not prompt_prefix:
        return ""
    return hashlib.sha256(f"{user_id}-{prompt_prefix}".encode("utf-8")).hexdigest()


def build_arp_session_id() -> str:
    now_ms = int(time.time() * 1000)
    ftr = f"{os.urandom(16).hex()}_{now_ms}_{os.getpid()}_dUAL43-mnts-ants-d4_31ck__tt"
    raw = json.dumps(
        {"sid": str(uuid.uuid4()), "ftr": ftr},
        separators=(",", ":"),
    )
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


# ---------------- 尺寸工具 ----------------

def video_size_from_ratio(ratio: str, resolution: str = "720p") -> dict:
    size_map = {
        "480p": {
            "21:9": (1120, 480), "16:9": (854, 480), "4:3": (640, 480),
            "1:1": (480, 480), "3:4": (480, 640), "9:16": (480, 854),
        },
        "720p": {
            "21:9": (1680, 720), "16:9": (1280, 720), "4:3": (960, 720),
            "1:1": (720, 720), "3:4": (720, 960), "9:16": (720, 1280),
        },
        "1080p": {
            "21:9": (2520, 1080), "16:9": (1920, 1080), "4:3": (1440, 1080),
            "1:1": (1080, 1080), "3:4": (1080, 1440), "9:16": (1080, 1920),
        },
    }
    res = str(resolution or "720p").strip().lower()
    table = size_map.get(res) or size_map["720p"]
    w, h = table.get(str(ratio)) or table["16:9"]
    return {"width": w, "height": h}


def image_size_from_ratio(ratio: str, resolution: str = "2K") -> dict:
    level = str(resolution or "2K").upper()
    if level == "1K":
        table = {
            "1:1": (1024, 1024), "16:9": (1360, 768), "9:16": (768, 1360),
            "4:3": (1152, 864), "3:4": (864, 1152), "4:1": (2048, 512),
            "1:4": (512, 2048), "8:1": (3072, 384), "1:8": (384, 3072),
        }
    elif level == "4K":
        table = {
            "1:1": (4096, 4096), "16:9": (5504, 3072), "9:16": (3072, 5504),
            "4:3": (4096, 3072), "3:4": (3072, 4096), "4:1": (8192, 2048),
            "1:4": (2048, 8192), "8:1": (12288, 1536), "1:8": (1536, 12288),
        }
    else:
        table = {
            "1:1": (2048, 2048), "16:9": (2752, 1536), "9:16": (1536, 2752),
            "4:3": (2048, 1536), "3:4": (1536, 2048), "4:1": (4096, 1024),
            "1:4": (1024, 4096), "8:1": (6144, 768), "1:8": (768, 6144),
        }
    w, h = table.get(str(ratio)) or table["16:9"]
    return {"width": w, "height": h}


# ---------------- 模型目录 ----------------

# ---------------- 模型目录（完整版，与 adobe2api 一致） ----------------
RATIO_SUFFIX_MAP = {
    "1:1": "1x1", "16:9": "16x9", "9:16": "9x16", "4:3": "4x3", "3:4": "3x4",
}
SEEDANCE_RATIO_SUFFIX_MAP = {**RATIO_SUFFIX_MAP, "21:9": "21x9"}
NANO_BANANA2_RATIO_SUFFIX_MAP = {
    **RATIO_SUFFIX_MAP, "1:8": "1x8", "1:4": "1x4", "4:1": "4x1", "8:1": "8x1",
}
GPT_IMAGE_RATIO_SUFFIX_MAP = {
    "1:1": "1x1", "5:4": "5x4", "9:16": "9x16", "21:9": "21x9", "16:9": "16x9",
    "3:2": "3x2", "4:3": "4x3", "4:5": "4x5", "3:4": "3x4", "2:3": "2x3",
}


def _build_image_models() -> dict:
    models = {}
    for prefix, uid, uver, family, ratio_map in [
        ("firefly-nano-banana-pro", "gemini-flash", "nano-banana-2", "Nano Banana Pro", RATIO_SUFFIX_MAP),
        ("firefly-nano-banana", "gemini-flash", "nano-banana-2", "Nano Banana", RATIO_SUFFIX_MAP),
        ("firefly-nano-banana2", "gemini-flash", "nano-banana-3", "Nano Banana 2", NANO_BANANA2_RATIO_SUFFIX_MAP),
    ]:
        for res in ("1k", "2k", "4k"):
            for ratio, suffix in ratio_map.items():
                models[f"{prefix}-{res}-{suffix}"] = {
                    "upstream_model_id": uid,
                    "upstream_model_version": uver,
                    "resolution": res.upper(),
                    "ratio": ratio,
                    "family": family,
                }
    for res in ("1k", "2k", "4k"):
        for ratio, suffix in GPT_IMAGE_RATIO_SUFFIX_MAP.items():
            models[f"firefly-gpt-image-{res}-{suffix}"] = {
                "upstream_model_id": "gpt-image",
                "upstream_model_version": "2",
                "resolution": res.upper(),
                "ratio": ratio,
                "family": "GPT Image",
            }
    return models


IMAGE_MODELS = _build_image_models()
DEFAULT_IMAGE_MODEL = "firefly-nano-banana-pro-2k-16x9"


def _build_video_models() -> dict:
    models = {}
    # sora2 / sora2-pro
    for engine, prefix, upstream in (
        ("sora2", "firefly-sora2", "openai:firefly:colligo:sora2"),
        ("sora2-pro", "firefly-sora2-pro", "openai:firefly:colligo:sora2-pro"),
    ):
        for dur in (4, 8, 12):
            for ratio in ("16:9", "9:16"):
                models[f"{prefix}-{dur}s-{RATIO_SUFFIX_MAP[ratio]}"] = {
                    "engine": engine, "duration": dur, "ratio": ratio,
                    "resolution": "720p", "upstream_model": upstream,
                }
    # gemini-omni
    for dur in (4, 6, 8, 10):
        for ratio in ("16:9", "9:16"):
            legacy = f"firefly-gemini-omni-{dur}s-{RATIO_SUFFIX_MAP[ratio]}"
            for res in ("720p", "1080p"):
                models[f"{legacy}-{res}"] = {
                    "engine": "gemini-omni", "duration": dur, "ratio": ratio,
                    "resolution": res,
                    "upstream_model_id": "gemini-omni",
                    "upstream_model_version": "omni-flash",
                }
            models[legacy] = dict(models[f"{legacy}-720p"])
    # veo31 / veo31-ref / veo31-fast
    for dur in (4, 6, 8):
        for ratio in ("16:9", "9:16"):
            for res in ("1080p", "720p"):
                for prefix, engine in (
                    ("firefly-veo31", "veo31-standard"),
                    ("firefly-veo31-ref", "veo31-standard"),
                    ("firefly-veo31-fast", "veo31-fast"),
                ):
                    conf = {
                        "engine": engine, "duration": dur, "ratio": ratio,
                        "resolution": res,
                        "reference_mode": "image" if prefix.endswith("-ref") else "frame",
                    }
                    models[f"{prefix}-{dur}s-{RATIO_SUFFIX_MAP[ratio]}-{res}"] = conf
    # kling-o3 / kling3
    for dur in (5, 15):
        for ratio in ("16:9", "9:16"):
            models[f"firefly-kling-o3-{dur}s-{RATIO_SUFFIX_MAP[ratio]}"] = {
                "engine": "kling-o3", "duration": dur, "ratio": ratio,
                "resolution": "1080p", "generate_audio": True,
            }
    for dur in (5, 10, 15):
        for ratio in ("16:9", "9:16"):
            models[f"firefly-kling3-{dur}s-{RATIO_SUFFIX_MAP[ratio]}"] = {
                "engine": "kling3", "duration": dur, "ratio": ratio,
                "resolution": "720p", "generate_audio": True,
            }
    # seedance20 / seedance20-fast
    for engine, version, prefix in (
        ("seedance", "seedance_2.0", "firefly-seedance20"),
        ("seedance-fast", "seedance_2.0_fast", "firefly-seedance20-fast"),
    ):
        for dur in range(4, 16):
            for ratio, suffix in SEEDANCE_RATIO_SUFFIX_MAP.items():
                for res in ("480p", "720p", "1080p"):
                    models[f"{prefix}-{dur}s-{suffix}-{res}"] = {
                        "engine": engine, "duration": dur, "ratio": ratio,
                        "resolution": res, "generate_audio": True,
                        "upstream_model_id": "seedance",
                        "upstream_model_version": version,
                    }
    # 兼容旧 ID：sora2 带 -720p 后缀
    models["firefly-sora2-8s-16x9-720p"] = {
        "engine": "sora2", "duration": 8, "ratio": "16:9",
        "resolution": "720p", "upstream_model": "openai:firefly:colligo:sora2",
    }
    models["firefly-sora2-8s-9x16-720p"] = {
        "engine": "sora2", "duration": 8, "ratio": "9:16",
        "resolution": "720p", "upstream_model": "openai:firefly:colligo:sora2",
    }
    return models


VIDEO_MODELS = _build_video_models()
DEFAULT_VIDEO_MODEL = "firefly-sora2-8s-16x9-720p"


class AdobeRequestError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None,
                 error_type: str = "", user_message: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = str(error_type or "").strip().lower()
        self.user_message = str(user_message or "").strip() or str(message or "").strip()


class AuthError(AdobeRequestError):
    pass


class AdobeClient:
    submit_url = "https://firefly-3p.ff.adobe.io/v2/3p-images/generate-async"
    video_submit_url = "https://firefly-3p.ff.adobe.io/v2/3p-videos/generate-async"
    upload_url = "https://firefly-3p.ff.adobe.io/v2/storage/image"
    video_upload_url = "https://firefly-3p.ff.adobe.io/v2/storage/video"

    def __init__(self) -> None:
        cfg = config_manager.get_all()
        self.api_key = str(cfg.get("api_key") or "clio-playground-web").strip() or "clio-playground-web"
        self.proxy = str(cfg.get("proxy") or "").strip() if cfg.get("use_proxy") else ""
        self.generate_timeout = int(cfg.get("generate_timeout") or 300)
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        )

    # ---------- 基础 ----------
    def _proxies(self) -> Optional[dict]:
        return {"http": self.proxy, "https": self.proxy} if self.proxy else None

    def _browser_headers(self) -> dict:
        return {
            "User-Agent": self.user_agent,
            "Origin": "https://firefly.adobe.com",
            "Referer": "https://firefly.adobe.com/",
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }

    def _submit_headers(self, token: str, prompt: str = "") -> dict:
        headers = self._browser_headers()
        headers.update({
            "Authorization": f"Bearer {token}",
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "*/*",
        })
        nonce = build_submit_nonce(token, prompt)
        if nonce:
            headers["x-nonce"] = nonce
        headers["x-arp-session-id"] = build_arp_session_id()
        return headers

    def _poll_headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "*/*",
            "Referer": "https://firefly.adobe.com/",
            "Origin": "https://firefly.adobe.com",
            "User-Agent": self.user_agent,
        }

    def _post_json(self, url: str, headers: dict, payload: dict):
        try:
            return requests.post(url, headers=headers, json=payload,
                                 timeout=60, proxies=self._proxies())
        except requests.Timeout as exc:
            raise AdobeRequestError(f"upstream timeout: {exc}", error_type="timeout")
        except requests.RequestException as exc:
            raise AdobeRequestError(f"upstream request error: {exc}", error_type="connection")

    # ---------- 上传 ----------
    @staticmethod
    def _extract_storage_id(data: Any, *keys: str) -> str:
        def walk(obj: Any) -> str:
            if isinstance(obj, dict):
                for key in keys + ("id", "assetId"):
                    val = obj.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
                    if isinstance(val, list):
                        for item in val:
                            got = walk(item)
                            if got:
                                return got
                    elif isinstance(val, dict):
                        got = walk(val)
                        if got:
                            return got
            elif isinstance(obj, list):
                for item in obj:
                    got = walk(item)
                    if got:
                        return got
            return ""

        return walk(data)

    def upload_file(self, file_path: Path, is_video: bool = False, token: str = "") -> str:
        token = token or self._require_token()["value"]
        url = self.video_upload_url if is_video else self.upload_url
        headers = self._browser_headers()
        headers.update({
            "Authorization": f"Bearer {token}",
            "x-api-key": self.api_key,
            "Accept": "*/*",
        })
        with open(file_path, "rb") as f:
            resp = requests.post(
                url, headers=headers, data=f.read(),
                timeout=120, proxies=self._proxies(),
            )
        if resp.status_code in (401, 403):
            raise AuthError("Token invalid or expired")
        if resp.status_code != 200:
            raise AdobeRequestError(
                f"upload failed: {resp.status_code} {resp.text[:300]}",
                status_code=resp.status_code,
            )
        try:
            data = resp.json()
        except Exception:
            raise AdobeRequestError("upload failed: invalid response")
        media_key = "videos" if is_video else "images"
        media_id = self._extract_storage_id(data, media_key, "assets", "asset")
        if not media_id:
            raise AdobeRequestError("upload succeeded but no media id returned")
        return media_id

    # ---------- Token ----------
    def _require_token(self) -> dict:
        strategy = str(config_manager.get("token_rotation_strategy", "round_robin"))
        token = token_manager.next_valid(strategy)
        if not token:
            raise AdobeRequestError(
                "没有可用的 Firefly token，请先在「设置」中导入 cookie 或添加 token",
                user_message="token_pool_empty",
            )
        return token

    # ---------- 提交与轮询 ----------
    def _extract_poll_url(self, resp: requests.Response, data: Any) -> str:
        link = str(resp.headers.get("x-override-status-link") or "").strip()
        if link:
            return link
        if isinstance(data, dict):
            links = data.get("links")
            if isinstance(links, dict):
                result = str(links.get("result") or "").strip()
                if result:
                    return result
            status = str(data.get("_links", {}).get("self") or "") if isinstance(data.get("_links"), dict) else ""
            if status:
                return status
        raise AdobeRequestError("submit response missing poll url")

    def _poll(self, poll_url: str, token: str, timeout: Optional[int] = None,
              on_progress: Optional[Callable[[dict], None]] = None) -> dict:
        timeout = timeout or self.generate_timeout
        headers = self._poll_headers(token)
        start = time.time()
        while True:
            try:
                resp = requests.get(poll_url, headers=headers, timeout=60,
                                    proxies=self._proxies())
            except requests.RequestException as exc:
                raise AdobeRequestError(f"poll request error: {exc}", error_type="connection")

            if resp.status_code in (401, 403):
                raise AuthError("Token invalid or expired")
            if resp.status_code != 200:
                raise AdobeRequestError(
                    f"poll failed: {resp.status_code} {resp.text[:300]}",
                    status_code=resp.status_code,
                )
            try:
                data = resp.json()
            except Exception:
                raise AdobeRequestError("poll response is not json")

            status = str(data.get("status") or "").lower()
            outputs = data.get("outputs") or []
            if outputs:
                return data
            if status in ("failed", "error", "cancelled"):
                raise AdobeRequestError(
                    f"generation failed: {json.dumps(data, ensure_ascii=False)[:500]}"
                )
            if on_progress:
                on_progress(data)
            if time.time() - start > timeout:
                raise AdobeRequestError(f"generation timeout after {timeout}s")
            time.sleep(2.5)

    # ---------- 生成 ----------
    def _run_submit_poll(self, payload: dict, prompt: str, is_video: bool,
                         token: dict, on_progress: Optional[Callable[[dict], None]] = None) -> dict:
        url = self.video_submit_url if is_video else self.submit_url
        headers = self._submit_headers(token["value"], prompt)
        try:
            resp = self._post_json(url, headers, payload)
        except AdobeRequestError as exc:
            token_manager.mark_fail(token["id"])
            raise

        if resp.status_code in (401, 403):
            token_manager.mark_fail(token["id"])
            raise AuthError("Token invalid or expired")
        if resp.status_code != 200:
            body = resp.text[:400]
            token_manager.mark_fail(token["id"])
            raise AdobeRequestError(
                f"submit failed: {resp.status_code} {body}",
                status_code=resp.status_code,
            )
        try:
            data = resp.json()
        except Exception:
            data = {}
        token_manager.mark_success(token["id"])

        poll_url = self._extract_poll_url(resp, data)
        if is_video:
            poll_url = self._normalize_video_poll_url(poll_url)
        return self._poll(poll_url, token["value"], on_progress=on_progress)

    # ---------- 轮询 URL 规范化（adobe2api：firefly-epo → bks-epo） ----------
    @staticmethod
    def _normalize_video_poll_url(raw_url: str) -> str:
        if not raw_url:
            return raw_url
        try:
            parsed = urlparse(raw_url)
            host = parsed.netloc
            path_parts = [p for p in parsed.path.split("/") if p]
            if not host or not path_parts:
                return raw_url
            if not host.startswith("firefly-epo"):
                return raw_url
            job_id = path_parts[-1]
            if not job_id:
                return raw_url
            host_suffix = host[len("firefly-epo"):].split(".", 1)[0]
            shard = host_suffix[:4].strip()
            if len(shard) != 4 or not shard.isdigit():
                return raw_url
            return f"https://bks-epo{shard}.adobe.io/v2/jobs/result/{job_id}?host={host}/"
        except Exception:
            return raw_url

    # ---------- 视频 payload 构造（与 adobe2api 一致） ----------
    @staticmethod
    def _build_video_prompt_json(prompt: str, duration: int, negative_prompt: str = "") -> str:
        payload = {
            "id": 1,
            "duration_sec": int(duration),
            "prompt_text": prompt,
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        return json.dumps(payload, ensure_ascii=False)

    def _build_video_payload(self, model: dict, prompt: str, ratio: str, duration: int,
                             resolution: str, init_image_id: Optional[str] = None,
                             ) -> dict:
        seed_val = int(time.time()) % 999999
        engine = str(model.get("engine") or "sora2")
        upstream_model = str(model.get("upstream_model") or "openai:firefly:colligo:sora2")

        if engine == "gemini-omni":
            reference_blobs = []
            if init_image_id:
                reference_blobs.append({"id": init_image_id, "usage": "style"})
            return {
                "modelId": "gemini-omni",
                "modelVersion": "omni-flash",
                "n": 1,
                "seeds": [seed_val],
                "prompt": prompt,
                "output": {"storeInputs": True},
                "referenceBlobs": reference_blobs,
                "generationMetadata": {"module": "aura"},
                "size": video_size_from_ratio(ratio, resolution),
                "duration": int(duration),
                "generationSettings": {"aspectRatio": ratio},
            }

        if engine in ("seedance", "seedance-fast"):
            payload = {
                "modelId": "seedance",
                "modelVersion": str(model.get("upstream_model_version") or
                                    ("seedance_2.0_fast" if engine == "seedance-fast" else "seedance_2.0")),
                "size": video_size_from_ratio(ratio, resolution),
                "seeds": [seed_val],
                "referenceBlobs": [],
                "prompt": prompt,
                "negativePrompt": "cartoon, vector art, & bad aesthetics & poor aesthetic",
                "duration": int(duration),
                "generateAudio": bool(model.get("generate_audio", True)),
                "generationMetadata": {"module": "text2video", "submodule": "ff-video-generate"},
                "generationSettings": {"aspectRatio": ratio},
                "output": {"storeInputs": True},
            }
            if init_image_id:
                payload["referenceBlobs"].append({"id": init_image_id, "usage": "style"})
            return payload

        if engine in ("veo31-fast", "veo31-standard"):
            model_version = "3.1-fast-generate" if engine == "veo31-fast" else "3.1-generate"
            payload = {
                "n": 1,
                "seeds": [seed_val],
                "modelId": "veo",
                "modelVersion": model_version,
                "output": {"storeInputs": True},
                "prompt": prompt,
                "size": video_size_from_ratio(ratio, resolution),
                "generateAudio": bool(model.get("generate_audio", True)),
                "referenceBlobs": [],
                "generationMetadata": {"module": "text2video"},
                "modelSpecificPayload": {
                    "parameters": {
                        "durationSeconds": int(duration),
                        "aspectRatio": ratio,
                        "addWaterMark": False,
                    }
                },
            }
            if init_image_id:
                if engine == "veo31-standard" and str(model.get("reference_mode")) == "image":
                    payload["referenceBlobs"].append({"id": init_image_id, "usage": "asset"})
                else:
                    payload["referenceBlobs"].append(
                        {"id": init_image_id, "usage": "general", "promptReference": 1})
            return payload

        if engine == "kling-o3":
            payload = {
                "n": 1,
                "seeds": [seed_val],
                "modelId": "kling",
                "modelVersion": "kling_o3_pro_reference_to_video",
                "output": {"storeInputs": True},
                "prompt": prompt,
                "size": video_size_from_ratio(ratio, resolution),
                "generateAudio": bool(model.get("generate_audio", True)),
                "generationMetadata": {
                    "module": "image2video" if init_image_id else "text2video",
                },
                "duration": int(duration),
                "generationSettings": {"aspectRatio": ratio},
                "referenceBlobs": [],
            }
            if init_image_id:
                payload["referenceBlobs"].append({"id": init_image_id, "usage": "frame", "order": 1})
            return payload

        if engine == "kling3":
            payload = {
                "n": 1,
                "seeds": [seed_val],
                "modelId": "kling",
                "modelVersion": "kling_v3_standard_i2v",
                "output": {"storeInputs": True},
                "prompt": prompt,
                "size": video_size_from_ratio(ratio, resolution),
                "generateAudio": bool(model.get("generate_audio", True)),
                "generationMetadata": {
                    "module": "image2video" if init_image_id else "text2video",
                },
                "duration": int(duration),
                "generationSettings": {"aspectRatio": ratio},
                "referenceBlobs": [],
            }
            if init_image_id:
                payload["referenceBlobs"].append({"id": init_image_id, "usage": "frame", "order": 1})
            return payload

        # sora2 / sora2-pro
        payload = {
            "n": 1,
            "seeds": [seed_val],
            "modelId": "sora",
            "modelVersion": "sora-2",
            "size": video_size_from_ratio(ratio, resolution),
            "duration": int(duration),
            "fps": 24,
            "prompt": self._build_video_prompt_json(prompt=prompt, duration=duration),
            "generationMetadata": {"module": "text2video"},
            "model": upstream_model,
            "generateAudio": True,
            "generateLoop": False,
            "transparentBackground": False,
            "seed": str(seed_val),
            "locale": "en-US",
            "camera": {
                "angle": "none", "shotSize": "none",
                "motion": None, "promptStyle": None,
            },
            "negativePrompt": "",
            "jobMode": "standard",
            "debugGenerationEndpoint": "",
            "referenceBlobs": [],
            "referenceFrames": [],
            "referenceVideo": None,
            "cameraMotionReferenceVideo": None,
            "characterReference": None,
            "editReferenceVideo": None,
            "output": {"storeInputs": True},
        }
        if init_image_id:
            first_id = init_image_id
            payload["referenceBlobs"] = [
                {"id": first_id, "usage": "general", "promptReference": 1},
            ]
            payload["referenceFrames"] = [{"localBlobRef": first_id}, None]
        return payload

    # ---------- 图像 payload 候选构造（与 adobe2api 一致） ----------
    @staticmethod
    def _gpt_image_pixels(ratio: str, resolution: str) -> dict:
        level = str(resolution or "2K").upper()
        if level == "1K":
            table = {
                "1:1": {"width": 1024, "height": 1024}, "5:4": {"width": 1120, "height": 896},
                "9:16": {"width": 720, "height": 1280}, "21:9": {"width": 1456, "height": 624},
                "16:9": {"width": 1280, "height": 720}, "4:3": {"width": 1152, "height": 864},
                "3:2": {"width": 1248, "height": 832}, "4:5": {"width": 896, "height": 1120},
                "3:4": {"width": 864, "height": 1152}, "2:3": {"width": 832, "height": 1248},
            }
        elif level == "4K":
            table = {
                "1:1": {"width": 2880, "height": 2880}, "5:4": {"width": 3200, "height": 2560},
                "9:16": {"width": 2160, "height": 3840}, "21:9": {"width": 3696, "height": 1584},
                "16:9": {"width": 3840, "height": 2160}, "4:3": {"width": 3264, "height": 2448},
                "3:2": {"width": 3504, "height": 2336}, "4:5": {"width": 2560, "height": 3200},
                "3:4": {"width": 2448, "height": 3264}, "2:3": {"width": 2336, "height": 3504},
            }
        else:
            table = {
                "1:1": {"width": 2048, "height": 2048}, "5:4": {"width": 2240, "height": 1792},
                "9:16": {"width": 1440, "height": 2560}, "21:9": {"width": 3024, "height": 1296},
                "16:9": {"width": 2560, "height": 1440}, "4:3": {"width": 2304, "height": 1728},
                "3:2": {"width": 2496, "height": 1664}, "4:5": {"width": 1792, "height": 2240},
                "3:4": {"width": 1728, "height": 2304}, "2:3": {"width": 1664, "height": 2496},
            }
        return table.get(str(ratio), table["16:9"])

    def _build_image_payload_candidates(self, prompt: str, model: dict, ratio: str,
                                        resolution: str, quality: str, seed: Optional[int],
                                        init_image_id: Optional[str] = None,
                                        ) -> list[dict]:
        seed_val = seed or int(time.time()) % 999999
        upstream_id = str(model["upstream_model_id"])
        upstream_ver = str(model["upstream_model_version"])

        if upstream_id.lower() == "gpt-image":
            detail = {"low": 1, "medium": 3, "high": 5}.get(str(quality).lower(), 3)
            pixel = self._gpt_image_pixels(ratio, resolution)
            base = {
                "modelId": upstream_id,
                "modelVersion": upstream_ver,
                "n": 1,
                "prompt": prompt,
                "seeds": [seed_val],
                "output": {"storeInputs": True},
                "referenceBlobs": [],
                "generationMetadata": {"module": "text2image", "submodule": "ff-image-generate"},
                "modelSpecificPayload": {"size": f"{pixel['width']}x{pixel['height']}"},
                "outputResolution": str(resolution or "2K").upper(),
                "generationSettings": {"detailLevel": detail},
            }
            base["size"] = pixel
            if not init_image_id:
                return [base]
            subject = dict(base)
            subject["referenceBlobs"] = [{"id": init_image_id, "usage": "subject"}]
            subject["modelSpecificPayload"] = {}
            ref_img = dict(base)
            ref_img["generationMetadata"] = {
                "module": "image2image", "submodule": "ff-image-generate",
            }
            ref_img["referenceBlobs"] = []
            ref_img["referenceImages"] = [{"id": init_image_id}]
            return [subject, ref_img]

        base = {
            "modelId": upstream_id,
            "modelVersion": upstream_ver,
            "n": 1,
            "prompt": prompt,
            "size": image_size_from_ratio(ratio, resolution),
            "seeds": [seed_val],
            "groundSearch": False,
            "skipCai": False,
            "output": {"storeInputs": True},
            "generationMetadata": {"module": "text2image", "submodule": "ff-image-generate"},
            "modelSpecificPayload": {"parameters": {"addWatermark": False}},
        }
        if ratio and ratio != "auto":
            base["modelSpecificPayload"]["aspectRatio"] = ratio
        if not init_image_id:
            base["referenceBlobs"] = []
            return [base]
        edited = dict(base)
        edited["generationMetadata"] = {
            "module": "image2image", "submodule": "ff-image-generate",
        }
        edited["referenceBlobs"] = [{"id": init_image_id, "usage": "general"}]
        return [edited]

    def generate_image(self, prompt: str, model_id: str = "firefly-nano-banana-pro-2k-16x9",
                       quality: str = "medium", seed: Optional[int] = None,
                       init_image_id: Optional[str] = None,
                       on_progress: Optional[Callable[[dict], None]] = None,
                       ) -> list[dict]:
        model = IMAGE_MODELS.get(model_id) or IMAGE_MODELS[DEFAULT_IMAGE_MODEL]
        ratio = model["ratio"]
        resolution = model["resolution"]

        payloads = self._build_image_payload_candidates(
            prompt, model, ratio, resolution, quality, seed, init_image_id)

        last_err: Optional[AdobeRequestError] = None
        for payload in payloads:
            try:
                token = self._require_token()
                data = self._run_submit_poll(payload, prompt, is_video=False,
                                             token=token, on_progress=on_progress)
                return self._collect_assets(data, "image")
            except AdobeRequestError as exc:
                last_err = exc
                if str(exc.error_type) in ("auth", "connection"):
                    continue
        if last_err:
            raise last_err
        return []

    def generate_video(self, prompt: str, model_id: str = "firefly-sora2-8s-16x9-720p",
                       init_image_id: Optional[str] = None,
                       on_progress: Optional[Callable[[dict], None]] = None,
                       ) -> list[dict]:
        model = VIDEO_MODELS.get(model_id) or VIDEO_MODELS[DEFAULT_VIDEO_MODEL]
        engine = model["engine"]
        ratio = model["ratio"]
        duration = int(model["duration"])
        resolution = model["resolution"]

        payload = self._build_video_payload(
            model, prompt, ratio, duration, resolution, init_image_id)

        token = self._require_token()
        data = self._run_submit_poll(payload, prompt, is_video=True,
                                     token=token, on_progress=on_progress)
        return self._collect_assets(data, "video")

    # ---------- 结果收集 ----------
    def _collect_assets(self, data: dict, kind: str) -> list[dict]:
        outputs = data.get("outputs") or []
        task_dir = OUTPUTS_DIR / time.strftime("%Y%m%d") / str(uuid.uuid4())[:8]
        task_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for idx, out in enumerate(outputs):
            url = None
            asset_id = ""
            if isinstance(out, dict):
                if out.get("url"):
                    url = out["url"]
                elif isinstance(out.get("asset"), dict) and out["asset"].get("url"):
                    url = out["asset"]["url"]
                if isinstance(out.get("asset"), dict):
                    asset_id = str(out["asset"].get("id") or "").strip()
                asset_id = asset_id or str(out.get("id") or "").strip()
            if not url:
                continue
            ext = ".mp4" if kind == "video" else ".png"
            save_path = task_dir / f"output_{idx + 1}{ext}"
            try:
                resp = requests.get(url, timeout=120, proxies=self._proxies())
                if resp.status_code == 200:
                    save_path.write_bytes(resp.content)
                    results.append({
                        "kind": kind,
                        "url": f"/outputs/{save_path.relative_to(OUTPUTS_DIR).as_posix()}",
                        "local_path": str(save_path),
                        "media_id": asset_id,
                        "seed": data.get("seed"),
                    })
            except requests.RequestException as exc:
                logger.warning("download asset failed: %s", exc)
        return results


adobe_client = AdobeClient()
