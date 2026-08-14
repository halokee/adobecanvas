"""简易请求日志存储：内存 + 文件持久化。"""

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = BASE_DIR / "config" / "request_logs.json"
MAX_MEMORY_LOGS = 5000


class LogStore:
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._logs: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        with self._lock:
            if LOG_FILE.exists():
                try:
                    raw = json.loads(LOG_FILE.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        self._logs = raw[-MAX_MEMORY_LOGS:]
                except Exception:
                    self._logs = []
            else:
                self._logs = []

    def _save(self) -> None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text(
            json.dumps(self._logs[-MAX_MEMORY_LOGS:], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def add(
        self,
        *,
        method: str,
        path: str,
        model: Optional[str] = None,
        status: int = 0,
        error: Optional[str] = None,
        duration_ms: int = 0,
        payload_preview: Optional[str] = None,
        response_preview: Optional[str] = None,
    ) -> None:
        entry = {
            "id": f"{int(time.time() * 1000)}-{len(self._logs)}",
            "time": int(time.time()),
            "method": method,
            "path": path,
            "model": model,
            "status": status,
            "error": error,
            "duration_ms": duration_ms,
            "payload_preview": payload_preview,
            "response_preview": response_preview,
        }
        with self._lock:
            self._logs.append(entry)
            self._logs = self._logs[-MAX_MEMORY_LOGS:]
            self._save()

    def list_logs(
        self,
        page: int = 1,
        per_page: int = 20,
        status: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            logs = list(self._logs)
        # filter
        filtered = []
        for log in reversed(logs):
            if status and str(log.get("status")) != status:
                continue
            if model and log.get("model") != model:
                continue
            filtered.append(log)
        total = len(filtered)
        start = (page - 1) * per_page
        end = start + per_page
        return {
            "total": total,
            "page": page,
            "per_page": per_page,
            "logs": filtered[start:end],
        }

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            logs = list(self._logs)
        total = len(logs)
        success = sum(1 for log in logs if str(log.get("status")).startswith("2"))
        error = sum(1 for log in logs if not str(log.get("status")).startswith("2"))
        running = sum(1 for log in logs if log.get("status") == 0)
        avg_duration = 0
        if logs:
            durations = [log.get("duration_ms", 0) for log in logs]
            avg_duration = int(sum(durations) / len(durations))
        return {
            "total": total,
            "success": success,
            "error": error,
            "running": running,
            "avg_duration_ms": avg_duration,
        }

    def running_tasks(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [log for log in self._logs if log.get("status") == 0]

    def get_by_id(self, log_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for log in self._logs:
                if log.get("id") == log_id:
                    return dict(log)
            return None


log_store = LogStore()
