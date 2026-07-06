import time
from pathlib import Path

from aiohttp import web
from server import PromptServer

from .nodes import (
    RUNTIME_LOG_DIR,
    _DIRECT_BACKENDS,
    _DIRECT_BACKENDS_LOCK,
    get_combined_memory_snapshot,
)
from .runtime_state import get_runtime_state_snapshot


ROUTES_REGISTERED = False


def _read_tail(path, max_chars=16000):
    if not path:
        return ""
    path = Path(path)
    if not path.exists() or not path.is_file():
        return ""
    data = path.read_bytes()
    if len(data) > max_chars:
        data = data[-max_chars:]
    return data.decode("utf-8", errors="replace")


def _latest_log_payload(max_chars=16000):
    log_dir = RUNTIME_LOG_DIR
    if not log_dir.exists():
        return {"path": "", "name": "", "tail": ""}
    files = sorted(
        [item for item in log_dir.iterdir() if item.is_file()],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return {"path": "", "name": "", "tail": ""}
    latest = files[0]
    return {
        "path": str(latest),
        "name": latest.name,
        "size": latest.stat().st_size,
        "mtime": latest.stat().st_mtime,
        "tail": _read_tail(latest, max_chars=max_chars),
    }


def _backend_statuses():
    statuses = []
    with _DIRECT_BACKENDS_LOCK:
        items = list(_DIRECT_BACKENDS.items())
    for cache_key, backend in items:
        try:
            status = backend.status()
        except Exception as error:
            status = {"healthy": False, "health_error": str(error)}
        statuses.append(
            {
                "cache_key": list(cache_key) if isinstance(cache_key, tuple) else str(cache_key),
                "status": status,
            }
        )
    return statuses


def _queue_summary(request=None):
    del request
    try:
        prompt_server = getattr(PromptServer, "instance", None)
        prompt_queue = getattr(prompt_server, "prompt_queue", None)
        if prompt_queue is None:
            return {"running": None, "pending": None, "error": "prompt_queue unavailable"}
        current_queue = prompt_queue.get_current_queue()
        running = current_queue[0] if current_queue and len(current_queue) > 0 else []
        pending = current_queue[1] if current_queue and len(current_queue) > 1 else []
        return {
            "running": len(running) if isinstance(running, list) else 0,
            "pending": len(pending) if isinstance(pending, list) else 0,
            "remaining": prompt_queue.get_tasks_remaining(),
        }
    except Exception as error:
        return {"running": None, "pending": None, "error": str(error)}


def build_monitor_payload(request=None):
    backend_statuses = _backend_statuses()
    active = None
    for item in backend_statuses:
        status = item.get("status", {})
        if status.get("healthy") or status.get("process_alive"):
            active = item
            break
    if active is None and backend_statuses:
        active = backend_statuses[0]
    return {
        "status": "ok",
        "timestamp": time.time(),
        "active_backend": active,
        "backend_count": len(backend_statuses),
        "backends": backend_statuses,
        "memory": get_combined_memory_snapshot(),
        "queue": _queue_summary(request),
        "latest_log": _latest_log_payload(),
        "task_agent": get_runtime_state_snapshot(),
    }


def register_task_agent_routes():
    global ROUTES_REGISTERED
    if ROUTES_REGISTERED:
        return True
    prompt_server = getattr(PromptServer, "instance", None)
    if prompt_server is None:
        return False
    routes = prompt_server.routes

    @routes.get("/studio-suite/task-agent/monitor/status")
    async def task_agent_monitor_status(request):
        try:
            return web.json_response(build_monitor_payload(request))
        except Exception as error:
            return web.json_response(
                {"status": "error", "error": str(error), "timestamp": time.time()},
                status=500,
            )

    ROUTES_REGISTERED = True
    return True
