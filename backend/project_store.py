"""项目存储：管理多个画布项目，每个项目保存画布 JSON 数据。

存储结构：
  data/projects.json            项目列表（元信息）
  data/canvases/{project_id}.json  每个项目的画布数据（nodes/edges/viewport）
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PROJECTS_FILE = DATA_DIR / "projects.json"
CANVASES_DIR = DATA_DIR / "canvases"

DEFAULT_PROJECT_NAME = "默认项目"

_lock = threading.Lock()


def _ensure_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CANVASES_DIR.mkdir(parents=True, exist_ok=True)
    if not PROJECTS_FILE.exists():
        PROJECTS_FILE.write_text(
            json.dumps({"projects": []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _load_projects_locked() -> list[dict]:
    try:
        raw = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        return list(raw.get("projects", []))
    except Exception:
        return []


def _save_projects_locked(projects: list[dict]) -> None:
    PROJECTS_FILE.write_text(
        json.dumps({"projects": projects}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_projects() -> list[dict]:
    """返回项目元信息列表（不含画布数据）。"""
    with _lock:
        _ensure_files()
        projects = _load_projects_locked()
        if not projects:
            projects = [{
                "id": f"proj_{uuid.uuid4().hex[:10]}",
                "name": DEFAULT_PROJECT_NAME,
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            }]
            _save_projects_locked(projects)
        return projects


def get_project(project_id: str) -> dict | None:
    with _lock:
        _ensure_files()
        for p in _load_projects_locked():
            if p["id"] == project_id:
                return p
        return None


def create_project(name: str = "") -> dict:
    with _lock:
        _ensure_files()
        projects = _load_projects_locked()
        project = {
            "id": f"proj_{uuid.uuid4().hex[:10]}",
            "name": (name or DEFAULT_PROJECT_NAME).strip()[:50] or DEFAULT_PROJECT_NAME,
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
        }
        projects.append(project)
        _save_projects_locked(projects)
        return project


def rename_project(project_id: str, name: str) -> dict | None:
    with _lock:
        _ensure_files()
        projects = _load_projects_locked()
        for p in projects:
            if p["id"] == project_id:
                p["name"] = (name or "").strip()[:50] or p["name"]
                p["updated_at"] = int(time.time())
                _save_projects_locked(projects)
                return p
        return None


def delete_project(project_id: str) -> bool:
    with _lock:
        _ensure_files()
        projects = _load_projects_locked()
        before = len(projects)
        projects = [p for p in projects if p["id"] != project_id]
        if len(projects) == before:
            return False
        _save_projects_locked(projects)
        canvas_file = CANVASES_DIR / f"{project_id}.json"
        if canvas_file.exists():
            canvas_file.unlink()
        return True


def get_canvas(project_id: str) -> dict:
    """读取项目画布；不存在时返回空画布。"""
    with _lock:
        _ensure_files()
        canvas_file = CANVASES_DIR / f"{project_id}.json"
        if canvas_file.exists():
            try:
                raw = json.loads(canvas_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
            except Exception:
                pass
        return {"version": 1, "nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "scale": 1}}


def save_canvas(project_id: str, canvas: dict) -> None:
    """保存项目画布。"""
    with _lock:
        _ensure_files()
        if not isinstance(canvas, dict):
            canvas = {}
        canvas.setdefault("version", 1)
        canvas.setdefault("nodes", [])
        canvas.setdefault("edges", [])
        canvas.setdefault("viewport", {"x": 0, "y": 0, "scale": 1})
        canvas_file = CANVASES_DIR / f"{project_id}.json"
        canvas_file.write_text(
            json.dumps(canvas, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 更新项目元信息 updated_at
        projects = _load_projects_locked()
        for p in projects:
            if p["id"] == project_id:
                p["updated_at"] = int(time.time())
                _save_projects_locked(projects)
                break
