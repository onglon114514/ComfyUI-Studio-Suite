import threading
import time
import uuid


_LOCK = threading.Lock()
_EVENTS = []
_ACTIVE_TASK = None
_LAST_OUTPUT = None
_MAX_EVENTS = 120
_MAX_STREAM_CHARS = 24000


def _truncate(value, max_chars=12000):
    text = "" if value is None else str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars // 2] + "\n...\n" + text[-max_chars // 2 :]


def _now():
    return time.time()


def record_task_event(stage, message="", task_id=None, task_type="", status="running", metadata=None, output_text=""):
    event = {
        "id": uuid.uuid4().hex[:12],
        "task_id": task_id or "",
        "stage": str(stage or ""),
        "status": str(status or ""),
        "task_type": str(task_type or ""),
        "message": _truncate(message, 2000),
        "metadata": metadata if isinstance(metadata, dict) else {},
        "output_text": _truncate(output_text, 12000),
        "timestamp": _now(),
    }
    with _LOCK:
        _EVENTS.append(event)
        if len(_EVENTS) > _MAX_EVENTS:
            del _EVENTS[: len(_EVENTS) - _MAX_EVENTS]
    return event


def start_task_event(kind, task_type="", metadata=None):
    task_id = uuid.uuid4().hex[:12]
    task = {
        "task_id": task_id,
        "kind": str(kind or ""),
        "task_type": str(task_type or ""),
        "status": "running",
        "message": "started",
        "metadata": metadata if isinstance(metadata, dict) else {},
        "started_at": _now(),
        "updated_at": _now(),
    }
    with _LOCK:
        global _ACTIVE_TASK
        _ACTIVE_TASK = task
    record_task_event("start", "started", task_id=task_id, task_type=task_type, metadata=metadata)
    return task_id


def update_task_event(task_id, stage, message="", task_type="", metadata=None):
    with _LOCK:
        if _ACTIVE_TASK and _ACTIVE_TASK.get("task_id") == task_id:
            _ACTIVE_TASK["stage"] = str(stage or "")
            _ACTIVE_TASK["message"] = _truncate(message, 2000)
            _ACTIVE_TASK["updated_at"] = _now()
            if task_type:
                _ACTIVE_TASK["task_type"] = str(task_type)
    record_task_event(stage, message, task_id=task_id, task_type=task_type, metadata=metadata)


def append_task_stream(task_id, text_delta, task_type="", stage="stream", metadata=None):
    delta = "" if text_delta is None else str(text_delta)
    if not delta:
        return
    with _LOCK:
        if _ACTIVE_TASK and _ACTIVE_TASK.get("task_id") == task_id:
            current = str(_ACTIVE_TASK.get("stream_text", "") or "")
            combined = current + delta
            if len(combined) > _MAX_STREAM_CHARS:
                combined = combined[-_MAX_STREAM_CHARS:]
            _ACTIVE_TASK["stream_text"] = combined
            _ACTIVE_TASK["stream_chars"] = int(_ACTIVE_TASK.get("stream_chars", 0) or 0) + len(delta)
            _ACTIVE_TASK["stage"] = str(stage or "stream")
            _ACTIVE_TASK["message"] = f"streaming... {int(_ACTIVE_TASK.get('stream_chars', 0) or 0)} chars"
            _ACTIVE_TASK["updated_at"] = _now()
            if task_type:
                _ACTIVE_TASK["task_type"] = str(task_type)
            if isinstance(metadata, dict) and metadata:
                existing = _ACTIVE_TASK.setdefault("stream_metadata", {})
                if isinstance(existing, dict):
                    existing.update(metadata)


def finish_task_event(task_id, status="success", message="", task_type="", output_text="", metadata=None):
    event = record_task_event(
        "finish",
        message,
        task_id=task_id,
        task_type=task_type,
        status=status,
        metadata=metadata,
        output_text=output_text,
    )
    with _LOCK:
        global _ACTIVE_TASK, _LAST_OUTPUT
        if _ACTIVE_TASK and _ACTIVE_TASK.get("task_id") == task_id:
            _ACTIVE_TASK = None
        _LAST_OUTPUT = {
            "task_id": task_id,
            "status": status,
            "task_type": task_type,
            "message": _truncate(message, 2000),
            "output_text": _truncate(output_text, 12000),
            "metadata": metadata if isinstance(metadata, dict) else {},
            "timestamp": event["timestamp"],
        }


def fail_task_event(task_id, message="", task_type="", metadata=None):
    finish_task_event(
        task_id,
        status="failed",
        message=message,
        task_type=task_type,
        output_text="",
        metadata=metadata,
    )


def get_runtime_state_snapshot():
    with _LOCK:
        return {
            "active_task": dict(_ACTIVE_TASK) if isinstance(_ACTIVE_TASK, dict) else None,
            "last_output": dict(_LAST_OUTPUT) if isinstance(_LAST_OUTPUT, dict) else None,
            "events": [dict(item) for item in _EVENTS[-40:]],
        }
