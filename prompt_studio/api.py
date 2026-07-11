from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import importlib.util
import subprocess
import atexit
import queue
import threading
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

from aiohttp import web
from server import PromptServer

import folder_paths

try:
    from PIL import Image, ImageOps
except Exception:
    Image = None
    ImageOps = None

try:
    from ruamel.yaml import YAML
except Exception:
    YAML = None

from .model_info import build_model_payload, save_model_notes


NODE_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = NODE_DIR.parent
PROMPT_STATIC_DIR = NODE_DIR / "frontend"
PROMPT_BUNDLE_DIR = NODE_DIR / "bundle"
PROMPT_BUNDLE_FILE = PROMPT_BUNDLE_DIR / "main.entry.js"
I18N_PATH = NODE_DIR / "i18n.json"

STORAGE_DIR = NODE_DIR / "storage"
AUTOCOMPLETE_DIR = STORAGE_DIR / "autocomplete"
GROUP_TAGS_DIR = STORAGE_DIR / "group_tags"
LOCAL_COMPLETE_TAGS_DIR = STORAGE_DIR / "local_complete_tags"
NOTES_DIR = STORAGE_DIR / "notes"
PROMPT_DATA_DIR = STORAGE_DIR / "prompt_data"
CUSTOM_WORDS_PATH = AUTOCOMPLETE_DIR / "custom_words.csv"
AUTOCOMPLETE_WORDS_PATH = AUTOCOMPLETE_DIR / "autocomplete.txt"

ROUTES_REGISTERED = False
_LLM_TRANSLATE_BACKEND = None
_LLM_TRANSLATE_MODULE = None
_PROMPT_STUDIO_PRIVATE_WORKER = None
_PROMPT_STUDIO_PRIVATE_WORKER_LOCK = threading.Lock()
_PROMPT_STUDIO_PRIVATE_WORKER_TIMER = None
_PROMPT_STUDIO_PROMPT_GUARD_REGISTERED = False
_TEXT_CACHE: dict[str, tuple[tuple[int, int], str]] = {}
_VALUE_CACHE: dict[str, tuple[float, object]] = {}
_FOLDER_LIST_CACHE_TTL = 20.0
_PREVIEW_EXTS = (".jpg", ".png", ".jpeg", ".gif", ".preview.jpg", ".preview.png", ".preview.jpeg", ".preview.gif")
_PREVIEW_THUMB_CACHE: dict[str, tuple[tuple[int, int], bytes, str]] = {}
_PREVIEW_THUMB_MAX_SIZE = 320


def _ensure_dirs() -> None:
    AUTOCOMPLETE_DIR.mkdir(parents=True, exist_ok=True)
    GROUP_TAGS_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_COMPLETE_TAGS_DIR.mkdir(parents=True, exist_ok=True)
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    PROMPT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROMPT_BUNDLE_DIR.mkdir(parents=True, exist_ok=True)


def _read_text_best_effort(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except Exception:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_text_cached(path: Path) -> str:
    """Cache large static text assets until mtime/size changes."""
    stat = path.stat()
    signature = (stat.st_mtime_ns, stat.st_size)
    cache_key = str(path)
    cached = _TEXT_CACHE.get(cache_key)
    if cached and cached[0] == signature:
        return cached[1]
    text = _read_text_best_effort(path)
    _TEXT_CACHE[cache_key] = (signature, text)
    return text


def _get_cached_value(key: str, ttl_seconds: float, builder):
    now = time.monotonic()
    cached = _VALUE_CACHE.get(key)
    if cached and now - cached[0] < ttl_seconds:
        return cached[1]
    value = builder()
    _VALUE_CACHE[key] = (now, value)
    return value


def _get_folder_filename_list(kind: str) -> list[str]:
    def build():
        try:
            return list(folder_paths.get_filename_list(kind))
        except Exception:
            return []

    return list(_get_cached_value(f"folder_paths:{kind}", _FOLDER_LIST_CACHE_TTL, build))


def _normalize_model_type(model_type: str) -> str:
    text = str(model_type or "").strip().lower()
    if text == "lora":
        return "loras"
    if text in {"embedding", "textual inversion", "textual_inversion"}:
        return "embeddings"
    return text


def _resolve_model_path(model_type: str, model_name: str) -> Path | None:
    normalized_type = _normalize_model_type(model_type)
    model_name = str(model_name or "").strip()
    if normalized_type not in {"loras", "embeddings"} or not model_name:
        return None

    lowered = model_name.replace("/", "\\").lower()
    try:
        filenames = folder_paths.get_filename_list(normalized_type)
    except Exception:
        return None

    for filename in filenames:
        filename_norm = str(filename).replace("/", "\\")
        stem_norm = os.path.splitext(filename_norm)[0]
        basename_stem = Path(filename_norm).stem
        candidates = {
            filename_norm.lower(),
            stem_norm.lower(),
            basename_stem.lower(),
        }
        if lowered in candidates:
            full_path = folder_paths.get_full_path(normalized_type, filename)
            return Path(full_path) if full_path else None
    return None


def _find_preview_path(model_path: Path | None) -> Path | None:
    if model_path is None:
        return None
    base = model_path.with_suffix("")
    for ext in _PREVIEW_EXTS:
        candidate = Path(str(base) + ext)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _model_preview_url(model_type: str, model_name: str) -> str | None:
    preview_path = _find_preview_path(_resolve_model_path(model_type, model_name))
    if preview_path is None:
        return None
    try:
        stat = preview_path.stat()
        version = f"{stat.st_mtime_ns:x}-{stat.st_size:x}"
    except Exception:
        version = str(int(time.time()))
    return "/studio-suite/prompt-studio/model-preview?" + urlencode({
        "type": _normalize_model_type(model_type),
        "name": model_name,
        "v": version,
    })


def _preview_response(preview_path: Path) -> web.StreamResponse:
    suffix = preview_path.suffix.lower()
    if Image is None or suffix == ".gif":
        response = web.FileResponse(preview_path)
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    stat = preview_path.stat()
    signature = (stat.st_mtime_ns, stat.st_size)
    cache_key = str(preview_path)
    cached = _PREVIEW_THUMB_CACHE.get(cache_key)
    if cached and cached[0] == signature:
        return web.Response(
            body=cached[1],
            content_type=cached[2],
            headers={"Cache-Control": "public, max-age=3600"},
        )

    try:
        with Image.open(preview_path) as img:
            if ImageOps is not None:
                img = ImageOps.exif_transpose(img)
            img.thumbnail((_PREVIEW_THUMB_MAX_SIZE, _PREVIEW_THUMB_MAX_SIZE))
            has_alpha = img.mode in {"RGBA", "LA"} or "transparency" in img.info
            buffer = BytesIO()
            if has_alpha:
                img.save(buffer, format="PNG", optimize=True)
                content_type = "image/png"
            else:
                img = img.convert("RGB")
                img.save(buffer, format="JPEG", quality=85, optimize=True)
                content_type = "image/jpeg"
            body = buffer.getvalue()
    except Exception:
        response = web.FileResponse(preview_path)
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    _PREVIEW_THUMB_CACHE[cache_key] = (signature, body, content_type)
    return web.Response(body=body, content_type=content_type, headers={"Cache-Control": "public, max-age=3600"})


def _lora_user_info_path(sha256: str) -> Path:
    return NOTES_DIR / "lorainfo" / f"{sha256}.json"


def _read_lora_user_info(sha256: str) -> dict:
    path = _lora_user_info_path(sha256)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_lora_user_info(sha256: str, data: dict) -> None:
    path = _lora_user_info_path(sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _legacy_lora_info_payload(file_name: str) -> dict | None:
    payload = build_model_payload("loras", file_name, NOTES_DIR)
    if payload is None:
        return None

    saved = _read_lora_user_info(payload["sha256"])
    preview_url = _model_preview_url("loras", file_name)
    images = [{"url": preview_url}] if preview_url else []
    metadata = payload.get("metadata") or {}
    trained_words = payload.get("trained_words") or []
    name = saved.get("name") or metadata.get("ss_output_name") or metadata.get("modelspec.title") or payload.get("name") or ""

    return {
        "file": file_name,
        "path": payload.get("path", ""),
        "name": name,
        "sha256": payload.get("sha256", ""),
        "baseModel": payload.get("base_model", ""),
        "baseModelFile": str(metadata.get("ss_sd_model_name") or ""),
        "images": images,
        "trainedWords": trained_words,
        "raw": {"metadata": metadata},
        "strengthMin": saved.get("strengthMin", ""),
        "strengthMax": saved.get("strengthMax", ""),
        "userNote": saved.get("userNote", payload.get("notes", "")),
        "loraWorks": saved.get("loraWorks", ""),
        "civitaiUrl": saved.get("civitaiUrl", ""),
    }


def _custom_words_source() -> Path | None:
    if CUSTOM_WORDS_PATH.exists():
        return CUSTOM_WORDS_PATH
    if AUTOCOMPLETE_WORDS_PATH.exists():
        return AUTOCOMPLETE_WORDS_PATH
    return None


def _sanitize_key(key: str) -> str:
    safe = []
    for ch in str(key):
        if ch.isalnum() or ch in ("_", "-", "."):
            safe.append(ch)
        else:
            safe.append("_")
    text = "".join(safe).strip("._")
    return text or "default"


def _storage_path_for_key(key: str) -> Path:
    safe = _sanitize_key(key)
    return PROMPT_DATA_DIR / f"{safe}.json"


def _storage_read_path_for_key(key: str) -> Path:
    return _storage_path_for_key(key)


def _storage_get(key: str):
    path = _storage_read_path_for_key(key)
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        return json.loads(_read_text_best_effort(path))
    except Exception:
        return None


def _storage_set(key: str, data) -> Path:
    path = _storage_path_for_key(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _storage_delete(key: str) -> None:
    path = _storage_path_for_key(key)
    if path.exists():
        path.unlink()


def _storage_get_list(key: str) -> list:
    data = _storage_get(key)
    return data if isinstance(data, list) else []


def _storage_list_push(key: str, item):
    data = _storage_get_list(key)
    data.append(item)
    _storage_set(key, data)
    return item


def _storage_list_pop(key: str):
    data = _storage_get_list(key)
    item = data.pop() if data else None
    _storage_set(key, data)
    return item


def _storage_list_shift(key: str):
    data = _storage_get_list(key)
    item = data.pop(0) if data else None
    _storage_set(key, data)
    return item


def _storage_list_remove(key: str, index: int):
    data = _storage_get_list(key)
    if 0 <= index < len(data):
        data.pop(index)
    _storage_set(key, data)


def _storage_list_get(key: str, index: int):
    data = _storage_get_list(key)
    if 0 <= index < len(data):
        return data[index]
    return None


def _storage_list_clear(key: str):
    _storage_set(key, [])


def _list_loras() -> list[str]:
    names = []
    for filename in _get_folder_filename_list("loras"):
        names.append(os.path.splitext(filename)[0])
    return sorted(set(names), key=str.lower)


def _list_embeddings() -> list[str]:
    return sorted({os.path.splitext(name)[0] for name in _get_folder_filename_list("embeddings")}, key=str.lower)


def _build_extra_networks():
    lora_items = []
    for item_path in _get_folder_filename_list("loras"):
        full_path = folder_paths.get_full_path("loras", item_path)
        model_name = os.path.splitext(item_path)[0]
        file_name = os.path.basename(item_path)
        dirname = os.path.dirname(full_path) if full_path else ""
        lora_items.append({
            "basename": item_path,
            "name": item_path,
            "dirname": dirname,
            "filename": full_path or item_path,
            "description": "",
            "preview": _model_preview_url("loras", item_path),
            "model_name": model_name,
            "model_type": "loras",
            "model_filename": file_name,
            "output_name": model_name,
            "prompt": f"<lora:{item_path}:",
            "local_info": None,
        })
    embedding_items = []
    for item_path in _get_folder_filename_list("embeddings"):
        full_path = folder_paths.get_full_path("embeddings", item_path)
        model_name = os.path.splitext(item_path)[0]
        file_name = os.path.basename(item_path)
        dirname = os.path.dirname(full_path) if full_path else ""
        embedding_items.append({
            "basename": item_path,
            "name": item_path,
            "dirname": dirname,
            "filename": full_path or item_path,
            "description": "",
            "preview": _model_preview_url("embeddings", item_path),
            "model_name": model_name,
            "model_type": "embeddings",
            "model_filename": file_name,
            "output_name": model_name,
            "prompt": model_name,
            "local_info": None,
        })
    result = []
    if lora_items:
        result.append({"name": "lora", "title": "Lora", "items": lora_items})
    if embedding_items:
        result.append({"name": "textual inversion", "title": "Embedding", "items": embedding_items})
    return result


def _extra_networks_response_text() -> str:
    def build() -> str:
        return json.dumps(
            {"extra_networks": _build_extra_networks()},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    return str(_get_cached_value("prompt_studio:extra_networks_response_text", _FOLDER_LIST_CACHE_TTL, build))


def _safe_join(root: Path, relative: str) -> Path | None:
    rel = str(relative or "").replace("\\", "/").lstrip("/")
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except Exception:
        return None
    return candidate


def _load_group_tags(lang: str) -> str:
    source_dir = GROUP_TAGS_DIR
    if not source_dir.exists():
        return ""

    def get_tags_file(name: str) -> Path:
        return source_dir / f"{name}.yaml"

    tags_file = get_tags_file("custom")
    custom_valid = False
    if tags_file.exists():
        try:
            custom_valid = bool(_read_text_best_effort(tags_file).strip())
        except Exception:
            custom_valid = False
    if not custom_valid:
        tags_file = get_tags_file(lang)
        if not tags_file.exists():
            tags_file = get_tags_file("default")
    if not tags_file.exists():
        return ""

    parts: list[str] = []
    for extra_name in ("prepend", None, "append"):
        try:
            path = tags_file if extra_name is None else get_tags_file(extra_name)
            if path.exists():
                text = _read_text_cached(path).strip()
                if text:
                    parts.append(text)
        except Exception:
            continue
    return "\n\n".join(parts)


def _group_tags_dir() -> Path:
    return GROUP_TAGS_DIR


def _group_tags_file(name: str) -> Path:
    return _group_tags_dir() / f"{name}.yaml"


def _group_tags_active_file(lang: str) -> Path | None:
    source_dir = _group_tags_dir()
    if not source_dir.exists():
        return None

    custom = source_dir / "custom.yaml"
    if custom.exists():
        try:
            if _read_text_best_effort(custom).strip():
                return custom
        except Exception:
            pass

    lang_file = source_dir / f"{lang}.yaml"
    if lang_file.exists():
        return lang_file
    default_file = source_dir / "default.yaml"
    return default_file if default_file.exists() else None


def _yaml_instance():
    if YAML is None:
        raise RuntimeError("ruamel.yaml is required for group tag editing")
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 100000
    return yaml


def _ensure_custom_group_tags_file(lang: str) -> Path:
    GROUP_TAGS_DIR.mkdir(parents=True, exist_ok=True)
    custom = GROUP_TAGS_DIR / "custom.yaml"
    if custom.exists():
        try:
            if _read_text_best_effort(custom).strip():
                return custom
        except Exception:
            pass

    source = _group_tags_active_file(lang)
    if source is not None and source.exists():
        custom.write_text(_read_text_best_effort(source), encoding="utf-8")
    else:
        custom.write_text("[]\n", encoding="utf-8")
    return custom


def _load_editable_group_tags(lang: str):
    yaml = _yaml_instance()
    path = _ensure_custom_group_tags_file(lang)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.load(handle)
    if data is None:
        data = []
    if not isinstance(data, list):
        raise ValueError("group tag YAML root must be a list")
    return yaml, path, data


def _save_editable_group_tags(yaml, path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)
    _TEXT_CACHE.pop(str(path), None)
    _VALUE_CACHE.pop("prompt_studio:extra_networks_response_text", None)


def _find_group(data, group_name: str):
    for group in data:
        if isinstance(group, dict) and group.get("name") == group_name:
            return group
    return None


def _find_subgroup(group, subgroup_name: str):
    if not isinstance(group, dict):
        return None
    groups = group.setdefault("groups", [])
    for subgroup in groups:
        if isinstance(subgroup, dict) and subgroup.get("name") == subgroup_name:
            return subgroup
    return None


def _group_tag_response(ok: bool = True, **extra):
    payload = {"info": "ok" if ok else "error", "success": bool(ok)}
    payload.update(extra)
    return web.json_response(payload, status=200 if ok else 400)


async def _request_json_dict(request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _list_csvs():
    csvs = []
    seen = set()
    for file in sorted(LOCAL_COMPLETE_TAGS_DIR.glob("*.csv")):
        if file.name in seen:
            continue
        seen.add(file.name)
        csvs.append({
            "key": file.name,
            "name": file.name,
            "size": file.stat().st_size,
            "path": str(file),
        })
    return csvs


def _resolve_csv_path(key: str) -> Path | None:
    if not key:
        return None
    candidate = LOCAL_COMPLETE_TAGS_DIR / Path(key).name
    return candidate if candidate.exists() else None


def _load_json_file(path: Path, default):
    try:
        if path.exists():
            return json.loads(_read_text_best_effort(path))
    except Exception:
        pass
    return default


def _load_i18n():
    return _load_json_file(I18N_PATH, {"default": "zh_CN", "languages": []})


def _load_translate_apis():
    # Legacy Prompt Studio frontend expects a list here.
    return []


def _task_agent_config_path() -> Path:
    local_path = PACKAGE_DIR / "config" / "task_agent_config.local.json"
    if local_path.exists():
        return local_path
    example_path = PACKAGE_DIR / "config" / "task_agent_config.example.json"
    if example_path.exists():
        return example_path
    return local_path


def _load_task_agent_gateway_module():
    global _LLM_TRANSLATE_MODULE
    if _LLM_TRANSLATE_MODULE is not None:
        return _LLM_TRANSLATE_MODULE
    module_path = PACKAGE_DIR / "task_agent_gateway.py"
    if not module_path.exists():
        raise RuntimeError(f"task_agent_gateway.py not found: {module_path}")
    spec = importlib.util.spec_from_file_location("prompt_studio_task_agent_gateway", str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load task_agent_gateway.py: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _LLM_TRANSLATE_MODULE = module
    return module


def _get_llm_translate_backend():
    global _LLM_TRANSLATE_BACKEND
    if _LLM_TRANSLATE_BACKEND is not None:
        return _LLM_TRANSLATE_BACKEND
    module = _load_task_agent_gateway_module()
    config_path = _task_agent_config_path()
    if not config_path.exists():
        raise RuntimeError(
            "Task Agent config is missing. Copy config/task_agent_config.example.json "
            "to config/task_agent_config.local.json, or keep the example file in place."
        )
    config = module.load_json_file(config_path)
    _LLM_TRANSLATE_BACKEND = module.ManagedBackend(config)
    return _LLM_TRANSLATE_BACKEND


def _unload_llm_translate_backend():
    global _LLM_TRANSLATE_BACKEND
    if _LLM_TRANSLATE_BACKEND is None:
        return {"status": "idle"}
    backend = _LLM_TRANSLATE_BACKEND
    _LLM_TRANSLATE_BACKEND = None
    try:
        return backend.unload()
    except Exception as error:
        return {"status": "failed", "error": str(error)}


def _prompt_studio_queue_state() -> dict:
    prompt_server = getattr(PromptServer, "instance", None)
    prompt_queue = getattr(prompt_server, "prompt_queue", None)
    if prompt_queue is None:
        return {"busy": False, "running": 0, "pending": 0}
    try:
        running, pending = prompt_queue.get_current_queue_volatile()
        running_count = len(running or [])
        pending_count = len(pending or [])
        return {
            "busy": (running_count + pending_count) > 0,
            "running": running_count,
            "pending": pending_count,
        }
    except Exception:
        try:
            remaining = int(prompt_queue.get_tasks_remaining() or 0)
        except Exception:
            remaining = 0
        return {"busy": remaining > 0, "running": 0, "pending": remaining}


def _prompt_studio_worker_state() -> dict:
    worker = _PROMPT_STUDIO_PRIVATE_WORKER
    process = getattr(worker, "process", None) if worker is not None else None
    alive = bool(process is not None and process.poll() is None)
    return {"alive": alive, "pid": process.pid if alive else None}


def _prompt_studio_queue_guard_error() -> web.Response | None:
    queue_state = _prompt_studio_queue_state()
    if not queue_state["busy"]:
        return None
    return web.json_response(
        {
            "success": False,
            "error": "comfy_queue_busy",
            "message": "ComfyUI queue is running or waiting; Prompt Studio LLM is disabled.",
            "queue": queue_state,
        },
        status=409,
    )


def _pick_translation_text(response: dict) -> str:
    result = response.get("json_result", {}) if isinstance(response, dict) else {}
    if isinstance(result, dict):
        for key in ("translated_text", "formatted_prompt", "formatted_prompt_tags", "raw_text"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ("tag_list", "tag_list_en", "normalized_tags_en", "expanded_tags_en"):
            value = result.get(key)
            if isinstance(value, list) and value:
                return ", ".join(str(item).strip() for item in value if str(item).strip())
    raw_text = response.get("raw_text", "") if isinstance(response, dict) else ""
    return str(raw_text or "").strip()


def _default_prompt_studio_translate_profile() -> str | None:
    candidates = _prompt_studio_translate_profile_candidates()
    return candidates[0] if candidates else None


def _prompt_studio_translate_profile_candidates() -> list[str]:
    profiles = _load_backend_profiles_for_prompt_studio()
    preferred = (
        "gemma4_e2b_hauhau_q8",
        "gemma4_e2b_hauhau_q8_vision",
        "gemma4_e2b_q8",
        "gemma4_e4b_q4",
        "gemma4_e4b_hauhau_q4_vision",
        "gemma4_e4b_hauhau_q8_vision",
    )
    result = [key for key in preferred if key in profiles]
    for key in profiles:
        if key not in result:
            result.append(key)
    return result


def _load_backend_profiles_for_prompt_studio() -> dict:
    profiles_path = PACKAGE_DIR / "config" / "backend_profiles.json"
    example_path = PACKAGE_DIR / "config" / "backend_profiles.example.json"
    profiles = {}
    for path in (profiles_path, example_path):
        try:
            if path.exists():
                loaded = json.loads(_read_text_best_effort(path))
                if isinstance(loaded, dict):
                    profiles.update(loaded)
        except Exception:
            continue
    return profiles


def _prompt_studio_config_uses_inprocess() -> bool:
    try:
        module = _load_task_agent_gateway_module()
        config_path = _task_agent_config_path()
        if not config_path.exists():
            return False
        config = module.load_json_file(config_path)
        backend = config.get("backend", {}) if isinstance(config, dict) else {}
        provider = str(backend.get("provider", "") or "").strip()
        mode = str(backend.get("mode", "") or "").strip()
        return provider == "llama_cpp_python_inproc" or mode == "inprocess"
    except Exception:
        return False


def _text_only_model_path_for_profile(profile_key: str) -> str | None:
    """Prompt Studio inline translation is text-only; do not inherit mmproj."""
    profile = _load_backend_profiles_for_prompt_studio().get(profile_key)
    if not isinstance(profile, dict):
        return None
    if not profile.get("mmproj_path") and not profile.get("supports_vision"):
        return None
    model_path = str(profile.get("model_path", "") or "").strip()
    return model_path or None


def _private_llama_worker_script_path() -> Path:
    return PACKAGE_DIR / "task_agent_core" / "private_llama_worker.py"


def _private_llama_runtime_available() -> bool:
    runtime_root = PACKAGE_DIR / "runtime" / "python_libs" / "llama_cpp_python_cu130"
    return (
        _private_llama_worker_script_path().exists()
        and (runtime_root / "llama_cpp" / "__init__.py").exists()
        and (
            (runtime_root / "llama_cpp" / "lib" / "llama.dll").exists()
            or (runtime_root / "llama_cpp" / "lib" / "libllama.so").exists()
            or (runtime_root / "llama_cpp" / "lib" / "libllama.dylib").exists()
        )
    )


def _run_private_llama_worker(payload: dict, timeout_sec: int = 420) -> dict:
    script_path = _private_llama_worker_script_path()
    if not script_path.exists():
        raise RuntimeError(f"private llama worker missing: {script_path}")

    temp_dir = PACKAGE_DIR / "runtime" / "temp" / "prompt_studio_private_llama_worker"
    temp_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for key in ("TEMP", "TMP", "TMPDIR"):
        env[key] = str(temp_dir)

    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW

    completed = subprocess.run(
        [sys.executable, str(script_path)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_sec,
        env=env,
        creationflags=creationflags,
    )
    lines = [line.strip() for line in str(completed.stdout or "").splitlines() if line.strip()]
    json_line = None
    for line in reversed(lines):
        if line.startswith("{") and '"ok"' in line:
            json_line = line
            break
    if json_line is None:
        stderr_tail = str(completed.stderr or "")[-2000:]
        stdout_tail = str(completed.stdout or "")[-2000:]
        raise RuntimeError(
            "private llama worker did not return JSON. "
            f"exit={completed.returncode} stderr_tail={stderr_tail} stdout_tail={stdout_tail}"
        )
    result = json.loads(json_line)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "private llama worker failed"))
    worker_result = result.get("result")
    if not isinstance(worker_result, dict):
        raise RuntimeError("private llama worker returned invalid result")
    return worker_result


class _PromptStudioPrivateWorker:
    def __init__(self, script_path: Path):
        self.script_path = script_path
        self.process = None
        self.lines = queue.Queue()
        self.stderr_lines = queue.Queue()
        self.reader_thread = None
        self.stderr_thread = None
        self.request_lock = threading.Lock()

    def _reader(self, pipe, target_queue):
        try:
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                target_queue.put(line.rstrip("\r\n"))
        except Exception as error:
            target_queue.put(json.dumps({"ok": False, "error": str(error), "reader_error": True}))

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        temp_dir = PACKAGE_DIR / "runtime" / "temp" / "prompt_studio_private_llama_daemon"
        temp_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for key in ("TEMP", "TMP", "TMPDIR"):
            env[key] = str(temp_dir)
        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW
        self.process = subprocess.Popen(
            [sys.executable, str(self.script_path), "--stdio-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PACKAGE_DIR),
            env=env,
            creationflags=creationflags,
            bufsize=1,
        )
        self.lines = queue.Queue()
        self.stderr_lines = queue.Queue()
        self.reader_thread = threading.Thread(target=self._reader, args=(self.process.stdout, self.lines), daemon=True)
        self.stderr_thread = threading.Thread(target=self._reader, args=(self.process.stderr, self.stderr_lines), daemon=True)
        self.reader_thread.start()
        self.stderr_thread.start()

    def request(self, payload: dict, timeout_sec: int = 420) -> dict:
        with self.request_lock:
            self.start()
            if self.process is None or self.process.stdin is None or self.process.poll() is not None:
                raise RuntimeError("Prompt Studio private worker is not running")
            request_id = str(uuid.uuid4())
            payload = dict(payload)
            payload["request_id"] = request_id
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

            deadline = time.time() + max(1, timeout_sec)
            last_lines = []
            while time.time() < deadline:
                remaining = max(0.05, deadline - time.time())
                try:
                    line = self.lines.get(timeout=min(0.5, remaining))
                except queue.Empty:
                    if self.process.poll() is not None:
                        break
                    continue
                if line:
                    last_lines.append(str(line)[-1000:])
                    if len(last_lines) > 8:
                        last_lines = last_lines[-8:]
                try:
                    response = json.loads(line)
                except Exception:
                    continue
                if response.get("request_id") != request_id:
                    continue
                if not response.get("ok"):
                    raise RuntimeError(str(response.get("error") or "private worker failed"))
                result = response.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("private worker returned invalid result")
                return result

            stderr_tail = []
            while not self.stderr_lines.empty() and len(stderr_tail) < 8:
                try:
                    stderr_tail.append(str(self.stderr_lines.get_nowait())[-1000:])
                except Exception:
                    break
            raise RuntimeError(
                "Prompt Studio private worker timed out or exited. "
                f"returncode={self.process.poll() if self.process else None}, "
                f"stdout_tail={' | '.join(last_lines[-4:])}, stderr_tail={' | '.join(stderr_tail[-4:])}"
            )

    def stop(self, timeout_sec: float = 3.0):
        process = self.process
        if process is None:
            return {"status": "idle"}
        try:
            if process.poll() is None and process.stdin is not None:
                request_id = str(uuid.uuid4())
                process.stdin.write(json.dumps({"command": "shutdown", "request_id": request_id}, ensure_ascii=False) + "\n")
                process.stdin.flush()
                deadline = time.time() + timeout_sec
                while time.time() < deadline:
                    try:
                        line = self.lines.get(timeout=0.2)
                    except queue.Empty:
                        if process.poll() is not None:
                            break
                        continue
                    try:
                        response = json.loads(line)
                    except Exception:
                        continue
                    if response.get("request_id") == request_id:
                        break
        except Exception:
            pass
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=timeout_sec)
                except Exception:
                    process.kill()
        finally:
            self.process = None
        return {"status": "stopped"}


def _stop_prompt_studio_private_worker():
    global _PROMPT_STUDIO_PRIVATE_WORKER, _PROMPT_STUDIO_PRIVATE_WORKER_TIMER
    with _PROMPT_STUDIO_PRIVATE_WORKER_LOCK:
        if _PROMPT_STUDIO_PRIVATE_WORKER_TIMER is not None:
            try:
                _PROMPT_STUDIO_PRIVATE_WORKER_TIMER.cancel()
            except Exception:
                pass
            _PROMPT_STUDIO_PRIVATE_WORKER_TIMER = None
        worker = _PROMPT_STUDIO_PRIVATE_WORKER
        _PROMPT_STUDIO_PRIVATE_WORKER = None
    if worker is not None:
        return worker.stop()
    return {"status": "idle"}


def _schedule_prompt_studio_private_worker_idle_shutdown(idle_seconds: int):
    global _PROMPT_STUDIO_PRIVATE_WORKER_TIMER
    if idle_seconds <= 0:
        return
    if _PROMPT_STUDIO_PRIVATE_WORKER_TIMER is not None:
        try:
            _PROMPT_STUDIO_PRIVATE_WORKER_TIMER.cancel()
        except Exception:
            pass
    timer = threading.Timer(float(idle_seconds), _stop_prompt_studio_private_worker)
    timer.daemon = True
    _PROMPT_STUDIO_PRIVATE_WORKER_TIMER = timer
    timer.start()


def _run_private_llama_worker_daemon(payload: dict, timeout_sec: int = 420, idle_seconds: int = 90) -> dict:
    global _PROMPT_STUDIO_PRIVATE_WORKER
    script_path = _private_llama_worker_script_path()
    if not script_path.exists():
        raise RuntimeError(f"private llama worker missing: {script_path}")
    with _PROMPT_STUDIO_PRIVATE_WORKER_LOCK:
        if _PROMPT_STUDIO_PRIVATE_WORKER is None:
            _PROMPT_STUDIO_PRIVATE_WORKER = _PromptStudioPrivateWorker(script_path)
        worker = _PROMPT_STUDIO_PRIVATE_WORKER
    result = worker.request(payload, timeout_sec=timeout_sec)
    with _PROMPT_STUDIO_PRIVATE_WORKER_LOCK:
        _schedule_prompt_studio_private_worker_idle_shutdown(int(idle_seconds))
    return result


atexit.register(_stop_prompt_studio_private_worker)


def _prompt_studio_should_use_private_worker(data: dict) -> bool:
    if str(data.get("backend_mode", "") or "").strip() == "managed_backend":
        return False
    if str(data.get("disable_private_worker", "") or "").lower() in {"1", "true", "yes"}:
        return False
    return _private_llama_runtime_available()


def _dynamic_prompt_studio_context_size(text: str, direction: str) -> int:
    length = len(str(text or ""))
    if direction.endswith("_text"):
        if length <= 160:
            return 512
        if length <= 520:
            return 1024
        if length <= 1200:
            return 1536
        return 2048
    if length <= 220:
        return 1024
    if length <= 900:
        return 1536
    return 2048


def _dynamic_prompt_studio_max_tokens(text: str, direction: str) -> int:
    length = len(str(text or ""))
    if direction.endswith("_text"):
        return max(64, min(160, 64 + length // 2))
    return max(160, min(640, 160 + length))


def _get_packages_state():
    # The retained legacy frontend iterates this value directly.
    return []


def _get_extensions():
    return []


def _get_extension_css_list():
    styles_extensions_dir = PROMPT_STATIC_DIR / "styles" / "extensions"
    if not styles_extensions_dir.exists():
        return []

    css_list = []
    for item in sorted(styles_extensions_dir.iterdir()):
        if not item.is_dir():
            continue
        manifest_path = item / "manifest.json"
        style_path = item / "style.min.css"
        if not manifest_path.exists() or not style_path.exists():
            continue
        css_list.append({
            "dir": item.name,
            "dataName": f"extensionSelect.{item.name}",
            "selected": bool(_storage_get(f"extensionSelect.{item.name}")),
            "manifest": _read_text_best_effort(manifest_path),
            "style": f"extensions/{item.name}/style.min.css",
        })
    return css_list


class _HistoryStore:
    def __init__(self):
        self.types = ["txt2img", "txt2img_neg", "img2img", "img2img_neg"]
        self.max_count = 100

    def _history_key(self, type_name: str) -> str:
        return f"history.{type_name}"

    def _favorite_key(self, type_name: str) -> str:
        return f"favorite.{type_name}"

    def get_histories(self, type_name: str):
        items = _storage_get_list(self._history_key(type_name))
        favorite_ids = {item.get("id") for item in self.get_favorites(type_name)}
        for item in items:
            item["is_favorite"] = item.get("id") in favorite_ids
        return items

    def get_favorites(self, type_name: str):
        return _storage_get_list(self._favorite_key(type_name))

    def _save_histories(self, type_name: str, items):
        _storage_set(self._history_key(type_name), items)

    def _save_favorites(self, type_name: str, items):
        _storage_set(self._favorite_key(type_name), items)

    def push_history(self, type_name: str, tags, prompt, name=""):
        import time
        import uuid
        items = self.get_histories(type_name)
        if len(items) >= self.max_count:
            items = items[-(self.max_count - 1):]
        item = {"id": str(uuid.uuid1()), "time": int(time.time()), "name": name, "tags": tags, "prompt": prompt}
        items.append(item)
        self._save_histories(type_name, items)
        return item

    def push_favorite(self, type_name: str, tags, prompt, name=""):
        import time
        import uuid
        items = self.get_favorites(type_name)
        item = {"id": str(uuid.uuid1()), "time": int(time.time()), "name": name, "tags": tags, "prompt": prompt}
        items.append(item)
        self._save_favorites(type_name, items)
        return item

    def move_up_favorite(self, type_name: str, item_id: str):
        items = self.get_favorites(type_name)
        for idx, item in enumerate(items):
            if item.get("id") == item_id:
                if idx > 0:
                    items.insert(idx - 1, items.pop(idx))
                    self._save_favorites(type_name, items)
                    return True
                return False
        return False

    def move_down_favorite(self, type_name: str, item_id: str):
        items = self.get_favorites(type_name)
        for idx, item in enumerate(items):
            if item.get("id") == item_id:
                if idx < len(items) - 1:
                    items.insert(idx + 1, items.pop(idx))
                    self._save_favorites(type_name, items)
                    return True
                return False
        return False

    def get_latest_history(self, type_name: str):
        items = self.get_histories(type_name)
        return items[-1] if items else None

    def set_history(self, type_name: str, item_id: str, tags, prompt, name):
        items = self.get_histories(type_name)
        changed = False
        for item in items:
            if item.get("id") == item_id:
                item.update({"tags": tags, "prompt": prompt, "name": name})
                changed = True
                break
        if changed:
            self._save_histories(type_name, items)
            self.set_favorite(type_name, item_id, tags, prompt, name)
        return changed

    def set_favorite(self, type_name: str, item_id: str, tags, prompt, name):
        items = self.get_favorites(type_name)
        changed = False
        for item in items:
            if item.get("id") == item_id:
                item.update({"tags": tags, "prompt": prompt, "name": name})
                changed = True
                break
        if changed:
            self._save_favorites(type_name, items)
        return changed

    def set_history_name(self, type_name: str, item_id: str, name: str):
        items = self.get_histories(type_name)
        changed = False
        for item in items:
            if item.get("id") == item_id:
                item["name"] = name
                changed = True
                break
        if changed:
            self._save_histories(type_name, items)
            self.set_favorite_name(type_name, item_id, name)
        return changed

    def set_favorite_name(self, type_name: str, item_id: str, name: str):
        items = self.get_favorites(type_name)
        changed = False
        for item in items:
            if item.get("id") == item_id:
                item["name"] = name
                changed = True
                break
        if changed:
            self._save_favorites(type_name, items)
        return changed

    def dofavorite(self, type_name: str, item_id: str):
        if any(item.get("id") == item_id for item in self.get_favorites(type_name)):
            return False
        for item in self.get_histories(type_name):
            if item.get("id") == item_id:
                favorites = self.get_favorites(type_name)
                favorites.append(item)
                self._save_favorites(type_name, favorites)
                return True
        return False

    def unfavorite(self, type_name: str, item_id: str):
        items = self.get_favorites(type_name)
        new_items = [item for item in items if item.get("id") != item_id]
        if len(new_items) == len(items):
            return False
        self._save_favorites(type_name, new_items)
        return True

    def remove_history(self, type_name: str, item_id: str):
        items = self.get_histories(type_name)
        new_items = [item for item in items if item.get("id") != item_id]
        if len(new_items) == len(items):
            return False
        self._save_histories(type_name, new_items)
        return True

    def remove_histories(self, type_name: str):
        self._save_histories(type_name, [])
        return True


def _guess_lang(request) -> str:
    return str(request.query.get("lang", "zh_CN") or "zh_CN")


def register_prompt_studio_routes():
    global ROUTES_REGISTERED, _PROMPT_STUDIO_PROMPT_GUARD_REGISTERED
    if ROUTES_REGISTERED:
        return True

    _ensure_dirs()
    prompt_server = getattr(PromptServer, "instance", None)
    if prompt_server is None:
        return False
    routes = prompt_server.routes
    history = _HistoryStore()

    if not _PROMPT_STUDIO_PROMPT_GUARD_REGISTERED:
        def release_prompt_studio_llm_before_queue(json_data):
            # This hook runs before ComfyUI accepts a workflow, so drawing never
            # races a resident Prompt Studio model for GPU/RAM.
            if _prompt_studio_worker_state()["alive"]:
                _stop_prompt_studio_private_worker()
            return json_data

        prompt_server.add_on_prompt_handler(release_prompt_studio_llm_before_queue)
        _PROMPT_STUDIO_PROMPT_GUARD_REGISTERED = True

    @routes.get("/studio-suite/prompt-studio/autocomplete/custom")
    async def prompt_studio_get_custom_words(request):
        source = _custom_words_source()
        if source is not None:
            return web.FileResponse(source)
        return web.Response(status=200, text="")

    @routes.post("/studio-suite/prompt-studio/autocomplete/custom")
    async def prompt_studio_save_custom_words(request):
        CUSTOM_WORDS_PATH.write_text(await request.text(), encoding="utf-8")
        return web.json_response({"status": "ok"})

    @routes.get("/studio-suite/prompt-studio/autocomplete/loras")
    async def prompt_studio_get_loras(request):
        return web.json_response(_list_loras())

    @routes.get("/studio-suite/prompt-studio/model-info")
    async def prompt_studio_get_model_info(request):
        model_type = str(request.query.get("type", "loras")).strip()
        model_name = str(request.query.get("name", "")).strip()
        payload = build_model_payload(model_type, model_name, NOTES_DIR)
        if payload is None:
            return web.json_response({"status": "error", "error": "model_not_found"}, status=404)
        return web.json_response({"status": "ok", "data": payload})

    @routes.get("/studio-suite/prompt-studio/model-preview")
    async def prompt_studio_get_model_preview(request):
        model_type = str(request.query.get("type", "loras")).strip()
        model_name = str(request.query.get("name", "")).strip()
        preview_path = _find_preview_path(_resolve_model_path(model_type, model_name))
        if preview_path is None:
            return web.Response(status=404, text="preview_not_found")
        return _preview_response(preview_path)

    @routes.post("/studio-suite/prompt-studio/model-info/notes")
    async def prompt_studio_save_model_notes(request):
        model_type = str(request.query.get("type", "loras")).strip()
        model_name = str(request.query.get("name", "")).strip()
        payload = save_model_notes(model_type, model_name, await request.text(), NOTES_DIR)
        if payload is None:
            return web.json_response({"status": "error", "error": "model_not_found"}, status=404)
        return web.json_response({"status": "ok", "data": payload})

    @routes.get("/weilin/lorainfo/api/loras/info")
    async def prompt_studio_legacy_lora_info(request):
        file_name = str(request.query.get("file", "")).strip()
        payload = _legacy_lora_info_payload(file_name)
        if payload is None:
            return web.json_response({"status": 404, "error": "No Lora found at path"}, status=404)
        return web.json_response({"status": 200, "data": payload})

    @routes.get("/weilin/lorainfo/api/loras/info/refresh")
    async def prompt_studio_legacy_lora_info_refresh(request):
        file_name = str(request.query.get("file", "")).strip()
        payload = _legacy_lora_info_payload(file_name)
        if payload is None:
            return web.json_response({"status": 404, "error": "No Lora found at path"}, status=404)
        return web.json_response({"status": 200, "data": payload})

    @routes.get("/weilin/lorainfo/api/loras/info/clear")
    async def prompt_studio_legacy_lora_info_clear(request):
        file_name = str(request.query.get("file", "")).strip()
        payload = build_model_payload("loras", file_name, NOTES_DIR)
        if payload is None:
            return web.json_response({"status": 404, "error": "No Lora found at path"}, status=404)
        path = _lora_user_info_path(payload["sha256"])
        if path.exists():
            path.unlink()
        return web.json_response({"status": 200, "data": _legacy_lora_info_payload(file_name)})

    @routes.post("/weilin/lorainfo/api/loras/info")
    async def prompt_studio_legacy_lora_info_save(request):
        file_name = str(request.query.get("file", "")).strip()
        payload = build_model_payload("loras", file_name, NOTES_DIR)
        if payload is None:
            return web.json_response({"status": 404, "error": "No Lora found at path"}, status=404)
        form = await request.post()
        raw_json = form.get("json", "{}")
        try:
            updates = json.loads(str(raw_json or "{}"))
        except Exception:
            updates = {}
        if not isinstance(updates, dict):
            updates = {}
        saved = _read_lora_user_info(payload["sha256"])
        for key in ("name", "strengthMin", "strengthMax", "userNote", "loraWorks", "civitaiUrl"):
            if key in updates:
                saved[key] = str(updates.get(key) or "")
        _write_lora_user_info(payload["sha256"], saved)
        return web.json_response({"status": 200, "data": _legacy_lora_info_payload(file_name)})

    @routes.post("/weilin/lorainfo/api/loras/set/img")
    async def prompt_studio_legacy_lora_set_img(request):
        form = await request.post()
        file_name = str(form.get("path", "")).strip()
        upload = form.get("image")
        source_name = str(form.get("fileName", "") or "preview.png")
        model_path = _resolve_model_path("loras", file_name)
        if model_path is None or upload is None or not hasattr(upload, "file"):
            return web.json_response({"status": 400, "error": "invalid_upload"}, status=400)
        ext = Path(source_name).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".gif"}:
            ext = ".png"
        target = model_path.with_suffix(ext)
        target.write_bytes(upload.file.read())
        _PREVIEW_THUMB_CACHE.pop(str(target), None)
        _VALUE_CACHE.pop("prompt_studio:extra_networks_response_text", None)
        return web.json_response({"status": 200, "data": _legacy_lora_info_payload(file_name)})

    @routes.get("/sd-webui-prompt-all-in-one-js")
    async def prompt_studio_legacy_bundle(request):
        if PROMPT_BUNDLE_FILE.exists():
            return web.Response(status=200, text=_read_text_cached(PROMPT_BUNDLE_FILE), content_type="application/javascript")
        return web.Response(status=404, text="legacy bundle not found")

    async def _serve_prompt_studio_ui(request):
        file_path = request.match_info.get("file_path", "")
        target = _safe_join(PROMPT_STATIC_DIR, file_path)
        if target and target.is_file():
            return web.FileResponse(target)
        raise web.HTTPNotFound()

    @routes.get("/studio-suite/prompt-studio/ui/{file_path:.*}")
    async def prompt_studio_static(request):
        return await _serve_prompt_studio_ui(request)

    @routes.get("/weilin/web_ui/{file_path:.*}")
    async def prompt_studio_legacy_static(request):
        return await _serve_prompt_studio_ui(request)

    async def _serve_style_file(request):
        rel = str(request.query.get("file", "")).strip()
        target = _safe_join(PROMPT_STATIC_DIR / "styles", rel)
        if target and target.is_file():
            return web.FileResponse(target)
        raise web.HTTPNotFound()

    @routes.get("/physton_prompt/styles")
    async def prompt_studio_styles_alias(request):
        return await _serve_style_file(request)

    @routes.get("/weilin/physton_prompt/styles")
    async def prompt_studio_styles_legacy(request):
        return await _serve_style_file(request)

    @routes.get("/weilin/physton_prompt/get_version")
    async def prompt_studio_get_version(request):
        return web.json_response({"version": "studio-suite-legacy-compat", "latest_version": "studio-suite-legacy-compat"})

    @routes.get("/weilin/physton_prompt/get_config")
    async def prompt_studio_get_config(request):
        return web.json_response({
            "i18n": _load_i18n(),
            "translate_apis": _load_translate_apis(),
            "packages_state": _get_packages_state(),
            "python": sys.executable,
        })

    @routes.get("/weilin/physton_prompt/get_extensions")
    async def prompt_studio_get_extensions(request):
        return web.json_response({"extensions": _get_extensions(), "extends": _get_extensions()})

    @routes.get("/weilin/physton_prompt/get_extension_css_list")
    async def prompt_studio_get_extension_css_list(request):
        return web.json_response({"css_list": _get_extension_css_list()})

    @routes.post("/weilin/physton_prompt/token_counter")
    async def prompt_studio_token_counter(request):
        data = await request.json()
        text = str(data.get("text", ""))
        token_count = len([part for part in text.replace("\n", " ").split(" ") if part.strip()])
        return web.json_response({"token_count": token_count, "max_length": 4096})

    @routes.post("/studio-suite/prompt-studio/llm_translate")
    async def prompt_studio_llm_translate(request):
        queue_error = _prompt_studio_queue_guard_error()
        if queue_error is not None:
            return queue_error
        data = await _request_json_dict(request)
        text = str(data.get("text", "") or "").strip()
        if not text:
            return web.json_response({"success": False, "error": "empty_text"}, status=400)

        direction = str(data.get("direction", "") or "zh_to_en_tags").strip()
        backend_profile = str(data.get("backend_profile", "") or "").strip() or _default_prompt_studio_translate_profile()
        dynamic_runtime = bool(data.get("dynamic_runtime", True))
        context_size = (
            _dynamic_prompt_studio_context_size(text, direction)
            if dynamic_runtime
            else int(data.get("context_size", 2048) or 2048)
        )
        max_tokens = (
            _dynamic_prompt_studio_max_tokens(text, direction)
            if dynamic_runtime
            else int(data.get("max_tokens", 512) or 512)
        )
        temperature = float(data.get("temperature", 0.22 if direction.endswith("_text") else 0.18) or 0.15)
        keep_warm = bool(data.get("keep_warm", True))
        unload_after_run = False if keep_warm else bool(data.get("unload_after_run", False))
        keep_warm_seconds = int(data.get("keep_warm_seconds", 600) or 600)
        custom_model_path = str(data.get("custom_model_path", "") or "").strip() or None
        use_private_worker = _prompt_studio_should_use_private_worker(data)
        if custom_model_path is None and (use_private_worker or _prompt_studio_config_uses_inprocess()):
            custom_model_path = _text_only_model_path_for_profile(backend_profile)

        try:
            task_inputs = {
                "raw_text": text,
                "direction": direction,
                "target_profile": str(data.get("target_profile", "") or "generic_tag_model"),
                "translation_mode": str(data.get("translation_mode", "") or "sentence").strip(),
                "purpose": "Prompt Studio inline translation for anime / Danbooru prompt editing.",
            }
            runtime_options = {
                "llama_cpp_python_n_gpu_layers": int(data.get("n_gpu_layers", 999) if dynamic_runtime else data.get("n_gpu_layers", 0) or 0),
                "llama_cpp_python_max_safe_gpu_layers": int(data.get("max_safe_gpu_layers", 8) or 8),
                "llama_cpp_python_n_batch": int(data.get("n_batch", 256 if dynamic_runtime else 128) or 128),
                "llama_cpp_python_threads": int(data.get("threads", 0) or 0),
            }
            if use_private_worker:
                worker_payload = {
                    "task_type": "translate_anime_tags",
                    "inputs": task_inputs,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "auto_load_backend": True,
                    "unload_after_run": unload_after_run,
                    "backend_profile": backend_profile,
                    "context_size": context_size,
                    "custom_model_path": custom_model_path,
                    "custom_mmproj_path": None,
                    "runtime_options": runtime_options,
                }
                if keep_warm:
                    response = await asyncio.to_thread(
                        _run_private_llama_worker_daemon,
                        worker_payload,
                        int(data.get("timeout_sec", 420) or 420),
                        keep_warm_seconds,
                    )
                else:
                    response = await asyncio.to_thread(_run_private_llama_worker, worker_payload)
            else:
                backend = _get_llm_translate_backend()
                response = await asyncio.to_thread(
                    backend.run_task,
                    task_type="translate_anime_tags",
                    inputs=task_inputs,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    auto_load_backend=True,
                    unload_after_run=unload_after_run,
                    backend_profile=backend_profile,
                    context_size=context_size,
                    custom_model_path=custom_model_path,
                    custom_mmproj_path=None,
                    runtime_options=runtime_options,
                )
            translated = _pick_translation_text(response)
            if not translated:
                return web.json_response(
                    {
                        "success": False,
                        "error": "empty_translation",
                        "raw_text": response.get("raw_text", "") if isinstance(response, dict) else "",
                    },
                    status=502,
                )
            return web.json_response({
                "success": True,
                "translated_text": translated,
                "source_text": response.get("json_result", {}).get("source_text", text) if isinstance(response, dict) else text,
                "translation_pairs": response.get("json_result", {}).get("translation_pairs", []) if isinstance(response, dict) else [],
                "raw_text": response.get("raw_text", "") if isinstance(response, dict) else "",
                "json_result": response.get("json_result", {}) if isinstance(response, dict) else {},
                "backend_mode": "private_llama_cpp_worker" if use_private_worker else "managed_backend",
                "backend_profile": backend_profile,
                "runtime": {
                    "dynamic_runtime": dynamic_runtime,
                    "keep_warm": keep_warm,
                    "keep_warm_seconds": keep_warm_seconds if keep_warm else 0,
                    "context_size": context_size,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "n_gpu_layers": runtime_options.get("llama_cpp_python_n_gpu_layers"),
                    "max_safe_gpu_layers": runtime_options.get("llama_cpp_python_max_safe_gpu_layers"),
                    "n_batch": runtime_options.get("llama_cpp_python_n_batch"),
                },
            })
        except Exception as error:
            if _prompt_studio_queue_state()["busy"]:
                return web.json_response(
                    {"success": False, "error": "comfy_queue_busy", "queue": _prompt_studio_queue_state()},
                    status=409,
                )
            return web.json_response({"success": False, "error": str(error)}, status=500)

    @routes.get("/studio-suite/prompt-studio/llm_status")
    async def prompt_studio_llm_status(request):
        return web.json_response({
            "success": True,
            "queue": _prompt_studio_queue_state(),
            "worker": _prompt_studio_worker_state(),
        })

    @routes.post("/studio-suite/prompt-studio/llm_preload")
    async def prompt_studio_llm_preload(request):
        queue_error = _prompt_studio_queue_guard_error()
        if queue_error is not None:
            return queue_error
        data = await _request_json_dict(request)
        if not _private_llama_runtime_available():
            return web.json_response({"success": False, "error": "private_llama_runtime_missing"}, status=503)
        backend_profile = str(data.get("backend_profile", "") or "").strip() or _default_prompt_studio_translate_profile()
        context_size = max(512, min(2048, int(data.get("context_size", 512) or 512)))
        custom_model_path = str(data.get("custom_model_path", "") or "").strip() or None
        if custom_model_path is None:
            custom_model_path = _text_only_model_path_for_profile(backend_profile)
        runtime_options = {
            "llama_cpp_python_n_gpu_layers": int(data.get("n_gpu_layers", 999) or 999),
            "llama_cpp_python_max_safe_gpu_layers": int(data.get("max_safe_gpu_layers", 8) or 8),
            "llama_cpp_python_n_batch": int(data.get("n_batch", 128) or 128),
            "llama_cpp_python_threads": int(data.get("threads", 0) or 0),
        }
        payload = {
            "command": "preload",
            "backend_profile": backend_profile,
            "context_size": context_size,
            "custom_model_path": custom_model_path,
            "runtime_options": runtime_options,
        }
        started_at = time.monotonic()
        try:
            result = await asyncio.to_thread(
                _run_private_llama_worker_daemon,
                payload,
                int(data.get("timeout_sec", 420) or 420),
                int(data.get("keep_warm_seconds", 600) or 600),
            )
            return web.json_response({
                "success": True,
                "result": result,
                "worker": _prompt_studio_worker_state(),
                "elapsed_seconds": round(time.monotonic() - started_at, 3),
            })
        except Exception as error:
            if _prompt_studio_queue_state()["busy"]:
                return web.json_response(
                    {"success": False, "error": "comfy_queue_busy", "queue": _prompt_studio_queue_state()},
                    status=409,
                )
            return web.json_response({"success": False, "error": str(error)}, status=500)

    @routes.post("/studio-suite/prompt-studio/llm_unload")
    async def prompt_studio_llm_unload(request):
        private_result = _stop_prompt_studio_private_worker()
        result = _unload_llm_translate_backend()
        if isinstance(result, dict):
            result["private_worker"] = private_result
        status = str(result.get("status", "") if isinstance(result, dict) else "").lower()
        ok = status not in {"failed", "error"}
        return web.json_response({"success": ok, "result": result}, status=200 if ok else 500)

    @routes.get("/weilin/physton_prompt/get_data")
    async def prompt_studio_get_data(request):
        key = str(request.query.get("key", "")).strip()
        return web.json_response({"data": _storage_get(key)})

    @routes.get("/weilin/physton_prompt/get_datas")
    async def prompt_studio_get_datas(request):
        keys = [item for item in str(request.query.get("keys", "")).split(",") if item]
        return web.json_response({"datas": {key: _storage_get(key) for key in keys}})

    @routes.post("/weilin/physton_prompt/set_data")
    async def prompt_studio_set_data(request):
        data = await request.json()
        key = str(data.get("key", "")).strip()
        if not key:
            return web.json_response({"success": False, "message": "key is required"}, status=400)
        _storage_set(key, data.get("data"))
        return web.json_response({"success": True})

    @routes.post("/weilin/physton_prompt/set_datas")
    async def prompt_studio_set_datas(request):
        payload = await request.json()
        data = payload.get("datas") if isinstance(payload, dict) and isinstance(payload.get("datas"), dict) else payload
        if not isinstance(data, dict):
            return web.json_response({"success": False, "message": "data must be a dict"}, status=400)
        for key, value in data.items():
            _storage_set(key, value)
        return web.json_response({"success": True})
    @routes.get("/weilin/physton_prompt/get_data_list_item")
    async def prompt_studio_get_data_list_item(request):
        key = str(request.query.get("key", "")).strip()
        try:
            index = int(request.query.get("index", "0"))
        except Exception:
            index = 0
        return web.json_response({"item": _storage_list_get(key, index)})

    @routes.post("/weilin/physton_prompt/push_data_list")
    async def prompt_studio_push_data_list(request):
        data = await request.json()
        key = str(data.get("key", "")).strip()
        if not key:
            return web.json_response({"success": False, "message": "key is required"}, status=400)
        _storage_list_push(key, data.get("item"))
        return web.json_response({"success": True})

    @routes.post("/weilin/physton_prompt/pop_data_list")
    async def prompt_studio_pop_data_list(request):
        data = await request.json()
        key = str(data.get("key", "")).strip()
        if not key:
            return web.json_response({"success": False, "message": "key is required"}, status=400)
        return web.json_response({"success": True, "item": _storage_list_pop(key)})

    @routes.post("/weilin/physton_prompt/shift_data_list")
    async def prompt_studio_shift_data_list(request):
        data = await request.json()
        key = str(data.get("key", "")).strip()
        if not key:
            return web.json_response({"success": False, "message": "key is required"}, status=400)
        return web.json_response({"success": True, "item": _storage_list_shift(key)})

    @routes.post("/weilin/physton_prompt/remove_data_list")
    async def prompt_studio_remove_data_list(request):
        data = await request.json()
        key = str(data.get("key", "")).strip()
        if not key:
            return web.json_response({"success": False, "message": "key is required"}, status=400)
        try:
            index = int(data.get("index", 0))
        except Exception:
            index = 0
        _storage_list_remove(key, index)
        return web.json_response({"success": True})

    @routes.post("/weilin/physton_prompt/clear_data_list")
    async def prompt_studio_clear_data_list(request):
        data = await request.json()
        key = str(data.get("key", "")).strip()
        if not key:
            return web.json_response({"success": False, "message": "key is required"}, status=400)
        _storage_list_clear(key)
        return web.json_response({"success": True})

    @routes.get("/weilin/physton_prompt/get_histories")
    async def prompt_studio_get_histories(request):
        type_name = str(request.query.get("type", "txt2img")).strip()
        return web.json_response({"histories": history.get_histories(type_name)})

    @routes.get("/weilin/physton_prompt/get_favorites")
    async def prompt_studio_get_favorites(request):
        type_name = str(request.query.get("type", "txt2img")).strip()
        return web.json_response({"favorites": history.get_favorites(type_name)})

    @routes.post("/weilin/physton_prompt/push_history")
    async def prompt_studio_push_history(request):
        data = await request.json()
        type_name = str(data.get("type", "")).strip()
        if not type_name:
            return web.json_response({"success": False, "message": "type is required"}, status=400)
        item = history.push_history(type_name, data.get("tags"), data.get("prompt"), data.get("name", ""))
        return web.json_response({"success": True, "item": item})

    @routes.post("/weilin/physton_prompt/push_favorite")
    async def prompt_studio_push_favorite(request):
        data = await request.json()
        type_name = str(data.get("type", "")).strip()
        if not type_name:
            return web.json_response({"success": False, "message": "type is required"}, status=400)
        item = history.push_favorite(type_name, data.get("tags"), data.get("prompt"), data.get("name", ""))
        return web.json_response({"success": True, "item": item})

    @routes.post("/weilin/physton_prompt/move_up_favorite")
    async def prompt_studio_move_up_favorite(request):
        data = await request.json()
        return web.json_response({"success": history.move_up_favorite(str(data.get("type", "")).strip(), str(data.get("id", "")).strip())})

    @routes.post("/weilin/physton_prompt/move_down_favorite")
    async def prompt_studio_move_down_favorite(request):
        data = await request.json()
        return web.json_response({"success": history.move_down_favorite(str(data.get("type", "")).strip(), str(data.get("id", "")).strip())})

    @routes.get("/weilin/physton_prompt/get_latest_history")
    async def prompt_studio_get_latest_history(request):
        type_name = str(request.query.get("type", "txt2img")).strip()
        return web.json_response({"history": history.get_latest_history(type_name)})

    @routes.post("/weilin/physton_prompt/set_history")
    async def prompt_studio_set_history(request):
        data = await request.json()
        return web.json_response({
            "success": history.set_history(
                str(data.get("type", "")).strip(),
                str(data.get("id", "")).strip(),
                data.get("tags"),
                data.get("prompt"),
                data.get("name", ""),
            )
        })

    @routes.post("/weilin/physton_prompt/set_history_name")
    async def prompt_studio_set_history_name(request):
        data = await request.json()
        return web.json_response({"success": history.set_history_name(str(data.get("type", "")).strip(), str(data.get("id", "")).strip(), data.get("name", ""))})

    @routes.post("/weilin/physton_prompt/set_favorite_name")
    async def prompt_studio_set_favorite_name(request):
        data = await request.json()
        return web.json_response({"success": history.set_favorite_name(str(data.get("type", "")).strip(), str(data.get("id", "")).strip(), data.get("name", ""))})

    @routes.post("/weilin/physton_prompt/dofavorite")
    async def prompt_studio_dofavorite(request):
        data = await request.json()
        return web.json_response({"success": history.dofavorite(str(data.get("type", "")).strip(), str(data.get("id", "")).strip())})

    @routes.post("/weilin/physton_prompt/unfavorite")
    async def prompt_studio_unfavorite(request):
        data = await request.json()
        return web.json_response({"success": history.unfavorite(str(data.get("type", "")).strip(), str(data.get("id", "")).strip())})

    @routes.post("/weilin/physton_prompt/delete_history")
    async def prompt_studio_delete_history(request):
        data = await request.json()
        return web.json_response({"success": history.remove_history(str(data.get("type", "")).strip(), str(data.get("id", "")).strip())})

    @routes.post("/weilin/physton_prompt/delete_histories")
    async def prompt_studio_delete_histories(request):
        data = await request.json()
        return web.json_response({"success": history.remove_histories(str(data.get("type", "")).strip())})

    @routes.get("/weilin/physton_prompt/get_group_tags")
    async def prompt_studio_get_group_tags(request):
        return web.json_response({"tags": _load_group_tags(_guess_lang(request))})

    @routes.post("/weilin/physton_prompt/add_group_tags")
    async def prompt_studio_add_group_tags(request):
        data = await _request_json_dict(request)
        try:
            yaml, path, tags = _load_editable_group_tags(_guess_lang(request))
            group = _find_group(tags, str(data.get("key", "")))
            subgroup = _find_subgroup(group, str(data.get("group", "")))
            if subgroup is None:
                return _group_tag_response(False, error="group_not_found")
            subgroup.setdefault("tags", {})[str(data.get("en", ""))] = str(data.get("cn", ""))
            _save_editable_group_tags(yaml, path, tags)
            return _group_tag_response()
        except Exception as e:
            return _group_tag_response(False, error=str(e))

    @routes.post("/weilin/physton_prompt/edit_group_tags")
    async def prompt_studio_edit_group_tags(request):
        data = await _request_json_dict(request)
        try:
            yaml, path, tags = _load_editable_group_tags(_guess_lang(request))
            group = _find_group(tags, str(data.get("key", "")))
            subgroup = _find_subgroup(group, str(data.get("group", "")))
            if subgroup is None:
                return _group_tag_response(False, error="group_not_found")
            subgroup.setdefault("tags", {})[str(data.get("en", ""))] = str(data.get("cn", ""))
            _save_editable_group_tags(yaml, path, tags)
            return _group_tag_response()
        except Exception as e:
            return _group_tag_response(False, error=str(e))

    @routes.post("/weilin/physton_prompt/delete_group_tags")
    async def prompt_studio_delete_group_tags(request):
        data = await _request_json_dict(request)
        try:
            yaml, path, tags = _load_editable_group_tags(_guess_lang(request))
            group = _find_group(tags, str(data.get("key", "")))
            subgroup = _find_subgroup(group, str(data.get("group", "")))
            if subgroup is None:
                return _group_tag_response(False, error="group_not_found")
            subgroup.setdefault("tags", {}).pop(str(data.get("en", "")), None)
            _save_editable_group_tags(yaml, path, tags)
            return _group_tag_response()
        except Exception as e:
            return _group_tag_response(False, error=str(e))

    @routes.post("/weilin/physton_prompt/new_node_group_tags")
    async def prompt_studio_new_node_group_tags(request):
        data = await _request_json_dict(request)
        try:
            yaml, path, tags = _load_editable_group_tags(_guess_lang(request))
            key = str(data.get("key", "")).strip()
            subgroup_name = str(data.get("group", "")).strip()
            if not key or not subgroup_name:
                return _group_tag_response(False, error="empty_group_name")
            if _find_group(tags, key) is None:
                tags.append({
                    "name": key,
                    "groups": [{
                        "name": subgroup_name,
                        "color": str(data.get("color", "")),
                        "tags": {},
                    }],
                })
            _save_editable_group_tags(yaml, path, tags)
            return _group_tag_response()
        except Exception as e:
            return _group_tag_response(False, error=str(e))

    @routes.post("/weilin/physton_prompt/new_group_tags")
    async def prompt_studio_new_group_tags(request):
        data = await _request_json_dict(request)
        try:
            yaml, path, tags = _load_editable_group_tags(_guess_lang(request))
            group = _find_group(tags, str(data.get("key", "")))
            subgroup_name = str(data.get("group", "")).strip()
            if group is None:
                return _group_tag_response(False, error="group_not_found")
            if not subgroup_name:
                return _group_tag_response(False, error="empty_group_name")
            if _find_subgroup(group, subgroup_name) is None:
                group.setdefault("groups", []).append({
                    "name": subgroup_name,
                    "color": str(data.get("color", "")),
                    "tags": {},
                })
            _save_editable_group_tags(yaml, path, tags)
            return _group_tag_response()
        except Exception as e:
            return _group_tag_response(False, error=str(e))

    @routes.post("/weilin/physton_prompt/edit_node_group_tags")
    async def prompt_studio_edit_node_group_tags(request):
        data = await _request_json_dict(request)
        try:
            yaml, path, tags = _load_editable_group_tags(_guess_lang(request))
            group = _find_group(tags, str(data.get("key", "")))
            new_name = str(data.get("group", "")).strip()
            if group is None:
                return _group_tag_response(False, error="group_not_found")
            if not new_name:
                return _group_tag_response(False, error="empty_group_name")
            group["name"] = new_name
            _save_editable_group_tags(yaml, path, tags)
            return _group_tag_response()
        except Exception as e:
            return _group_tag_response(False, error=str(e))

    @routes.post("/weilin/physton_prompt/edit_child_group_tags")
    async def prompt_studio_edit_child_group_tags(request):
        data = await _request_json_dict(request)
        try:
            yaml, path, tags = _load_editable_group_tags(_guess_lang(request))
            group = _find_group(tags, str(data.get("key", "")))
            subgroup = _find_subgroup(group, str(data.get("group", "")))
            new_name = str(data.get("newgroup", "")).strip()
            if subgroup is None:
                return _group_tag_response(False, error="group_not_found")
            if not new_name:
                return _group_tag_response(False, error="empty_group_name")
            subgroup["name"] = new_name
            _save_editable_group_tags(yaml, path, tags)
            return _group_tag_response()
        except Exception as e:
            return _group_tag_response(False, error=str(e))

    @routes.post("/weilin/physton_prompt/delete_node_group_tags")
    async def prompt_studio_delete_node_group_tags(request):
        data = await _request_json_dict(request)
        try:
            yaml, path, tags = _load_editable_group_tags(_guess_lang(request))
            key = str(data.get("key", ""))
            tags[:] = [group for group in tags if not (isinstance(group, dict) and group.get("name") == key)]
            _save_editable_group_tags(yaml, path, tags)
            return _group_tag_response()
        except Exception as e:
            return _group_tag_response(False, error=str(e))

    @routes.post("/weilin/physton_prompt/delete_child_group_tags")
    async def prompt_studio_delete_child_group_tags(request):
        data = await _request_json_dict(request)
        try:
            yaml, path, tags = _load_editable_group_tags(_guess_lang(request))
            group = _find_group(tags, str(data.get("key", "")))
            if group is None:
                return _group_tag_response(False, error="group_not_found")
            subgroup_name = str(data.get("group", ""))
            group["groups"] = [
                subgroup for subgroup in group.setdefault("groups", [])
                if not (isinstance(subgroup, dict) and subgroup.get("name") == subgroup_name)
            ]
            _save_editable_group_tags(yaml, path, tags)
            return _group_tag_response()
        except Exception as e:
            return _group_tag_response(False, error=str(e))

    @routes.get("/weilin/physton_prompt/get_csvs")
    async def prompt_studio_get_csvs(request):
        return web.json_response({"csvs": _list_csvs()})

    @routes.get("/weilin/physton_prompt/get_csv")
    async def prompt_studio_get_csv(request):
        path = _resolve_csv_path(str(request.query.get("key", "")).strip())
        if path is None or not path.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    @routes.get("/weilin/physton_prompt/get_extra_networks")
    async def prompt_studio_get_extra_networks(request):
        return web.Response(status=200, text=_extra_networks_response_text(), content_type="application/json")

    ROUTES_REGISTERED = True




