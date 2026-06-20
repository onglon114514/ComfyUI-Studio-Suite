from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from aiohttp import web
from server import PromptServer

import folder_paths

from .model_info import build_model_payload, save_model_notes


NODE_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = NODE_DIR.parent
CUSTOM_NODES_DIR = PACKAGE_DIR.parent
WEB_DIR = PACKAGE_DIR / "web"
PROMPT_STATIC_DIR = WEB_DIR / "prompt_static"
PROMPT_BUNDLE_DIR = WEB_DIR / "prompt_bundle"
PROMPT_BUNDLE_FILE = PROMPT_BUNDLE_DIR / "main.entry.js"

STORAGE_DIR = NODE_DIR / "storage"
AUTOCOMPLETE_DIR = STORAGE_DIR / "autocomplete"
GROUP_TAGS_DIR = STORAGE_DIR / "group_tags"
LOCAL_COMPLETE_TAGS_DIR = STORAGE_DIR / "local_complete_tags"
NOTES_DIR = STORAGE_DIR / "notes"
PROMPT_DATA_DIR = STORAGE_DIR / "prompt_data"
CUSTOM_WORDS_PATH = AUTOCOMPLETE_DIR / "custom_words.csv"
AUTOCOMPLETE_WORDS_PATH = AUTOCOMPLETE_DIR / "autocomplete.txt"

LEGACY_DISABLED_DIR = CUSTOM_NODES_DIR / ".disabled" / "weilin-comfyui-prompt-all-in-one-page-unlock"
LEGACY_SRC_DIR = LEGACY_DISABLED_DIR / "src"
LEGACY_CONFIG_DIR = LEGACY_SRC_DIR / "functional" / "config"
LEGACY_CUSTOM_WORDS_PATH = LEGACY_SRC_DIR / "prompt_storage" / "autocomplete" / "autocomplete" / "autocomplete.txt"
LEGACY_GROUP_TAGS_DIR = LEGACY_SRC_DIR / "prompt_storage" / "group_tags" / "group_tags"
LEGACY_LOCAL_COMPLETE_TAGS_DIR = LEGACY_SRC_DIR / "prompt_storage" / "local_complete_tags" / "local_complete_tags"
LEGACY_PROMPT_DATA_DIR = LEGACY_SRC_DIR / "prompt_storage" / "data" / "prompt_storage"
LEGACY_PROMPT_STATIC_DIR = LEGACY_SRC_DIR / "ui" / "web_static" / "prompt_static"
LEGACY_PROMPT_BUNDLE_FILE = LEGACY_SRC_DIR / "ui" / "web_bundle" / "prompt_js" / "main.entry.js"
LEGACY_I18N_PATH = LEGACY_CONFIG_DIR / "i18n.json"
LEGACY_TRANSLATE_APIS_PATH = LEGACY_CONFIG_DIR / "translate_apis.json"

ROUTES_REGISTERED = False


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


def _custom_words_source() -> Path | None:
    if CUSTOM_WORDS_PATH.exists():
        return CUSTOM_WORDS_PATH
    if AUTOCOMPLETE_WORDS_PATH.exists():
        return AUTOCOMPLETE_WORDS_PATH
    if LEGACY_CUSTOM_WORDS_PATH.exists():
        return LEGACY_CUSTOM_WORDS_PATH
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
    primary_path = _storage_path_for_key(key)
    if primary_path.exists():
        return primary_path
    legacy_path = LEGACY_PROMPT_DATA_DIR / f"{_sanitize_key(key)}.json"
    if legacy_path.exists():
        return legacy_path
    return primary_path


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


def _copy_legacy_bundle_if_needed() -> None:
    if PROMPT_BUNDLE_FILE.exists() or not LEGACY_PROMPT_BUNDLE_FILE.exists():
        return
    PROMPT_BUNDLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROMPT_BUNDLE_FILE.write_text(_read_text_best_effort(LEGACY_PROMPT_BUNDLE_FILE), encoding="utf-8")


def _list_loras() -> list[str]:
    names = []
    for filename in folder_paths.get_filename_list("loras"):
        names.append(Path(filename).stem)
    return sorted(set(names), key=str.lower)


def _list_embeddings() -> list[str]:
    try:
        return sorted({Path(name).stem for name in folder_paths.get_filename_list("embeddings")}, key=str.lower)
    except Exception:
        return []


def _build_extra_networks():
    lora_items = []
    for item_path in folder_paths.get_filename_list("loras"):
        model_name = Path(item_path).stem
        lora_items.append({
            "basename": item_path,
            "name": item_path,
            "model_name": model_name,
            "model_type": "loras",
            "model_filename": Path(item_path).name,
            "prompt": f"<lora:{item_path}:",
        })
    embedding_items = []
    for item_path in _list_embeddings():
        embedding_items.append({
            "basename": item_path,
            "name": item_path,
            "model_name": item_path,
            "model_type": "embeddings",
            "model_filename": item_path,
            "prompt": item_path,
        })
    result = []
    if lora_items:
        result.append({"name": "lora", "title": "Lora", "items": lora_items})
    if embedding_items:
        result.append({"name": "textual inversion", "title": "Embedding", "items": embedding_items})
    return result


def _safe_join(root: Path, relative: str) -> Path | None:
    rel = str(relative or "").replace("\\", "/").lstrip("/")
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except Exception:
        return None
    return candidate


def _load_group_tags(lang: str) -> str:
    source_dir = GROUP_TAGS_DIR if GROUP_TAGS_DIR.exists() else LEGACY_GROUP_TAGS_DIR
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
                text = _read_text_best_effort(path).strip()
                if text:
                    parts.append(text)
        except Exception:
            continue
    return "\n\n".join(parts)


def _list_csvs():
    csvs = []
    seen = set()
    for source_dir in (LOCAL_COMPLETE_TAGS_DIR, LEGACY_LOCAL_COMPLETE_TAGS_DIR):
        if not source_dir.exists():
            continue
        for file in sorted(source_dir.glob("*.csv")):
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
    for source_dir in (LOCAL_COMPLETE_TAGS_DIR, LEGACY_LOCAL_COMPLETE_TAGS_DIR):
        candidate = source_dir / Path(key).name
        if candidate.exists():
            return candidate
    return None


def _load_json_file(path: Path, default):
    try:
        if path.exists():
            return json.loads(_read_text_best_effort(path))
    except Exception:
        pass
    return default


def _load_i18n():
    return _load_json_file(LEGACY_I18N_PATH, {"default": "zh_CN", "languages": []})


def _load_translate_apis():
    # Legacy Prompt Studio frontend expects a list here.
    return []



def _get_packages_state():
    return {}


def _get_extensions():
    extensions_dir = CUSTOM_NODES_DIR / "extensions"
    if not extensions_dir.exists():
        return []
    result = []
    for item in sorted(extensions_dir.iterdir()):
        if item.is_dir():
            result.append(item.name)
    return result


def _get_extension_css_list():
    styles_extensions_dir = PROMPT_STATIC_DIR / "styles" / "extensions"
    if not styles_extensions_dir.exists():
        styles_extensions_dir = LEGACY_PROMPT_STATIC_DIR / "styles" / "extensions"
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
    global ROUTES_REGISTERED
    if ROUTES_REGISTERED:
        return True

    _ensure_dirs()
    _copy_legacy_bundle_if_needed()
    prompt_server = getattr(PromptServer, "instance", None)
    if prompt_server is None:
        return False
    routes = prompt_server.routes
    history = _HistoryStore()

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

    @routes.post("/studio-suite/prompt-studio/model-info/notes")
    async def prompt_studio_save_model_notes(request):
        model_type = str(request.query.get("type", "loras")).strip()
        model_name = str(request.query.get("name", "")).strip()
        payload = save_model_notes(model_type, model_name, await request.text(), NOTES_DIR)
        if payload is None:
            return web.json_response({"status": "error", "error": "model_not_found"}, status=404)
        return web.json_response({"status": "ok", "data": payload})

    @routes.get("/sd-webui-prompt-all-in-one-js")
    async def prompt_studio_legacy_bundle(request):
        if PROMPT_BUNDLE_FILE.exists():
            return web.Response(status=200, text=_read_text_best_effort(PROMPT_BUNDLE_FILE), content_type="application/javascript")
        return web.Response(status=404, text="legacy bundle not found")

    @routes.get("/weilin/web_ui/{file_path:.*}")
    async def prompt_studio_legacy_static(request):
        file_path = request.match_info.get("file_path", "")
        root = PROMPT_STATIC_DIR if PROMPT_STATIC_DIR.exists() else LEGACY_PROMPT_STATIC_DIR
        target = _safe_join(root, file_path)
        if target and target.is_file():
            return web.FileResponse(target)
        raise web.HTTPNotFound()

    async def _serve_style_file(request):
        rel = str(request.query.get("file", "")).strip()
        target = _safe_join(PROMPT_STATIC_DIR / "styles", rel)
        if target is None or not target.is_file():
            target = _safe_join(LEGACY_PROMPT_STATIC_DIR / "styles", rel)
        if target and target.is_file():
            return web.FileResponse(target)
        raise web.HTTPNotFound()

    @routes.get("/physton_prompt/styles")
    async def prompt_studio_styles_alias(request):
        return await _serve_style_file(request)

    @routes.get("/weilin/physton_prompt/styles")
    async def prompt_studio_styles_legacy(request):
        return await _serve_style_file(request)

    def _try_register_exact_legacy_backend() -> bool:
        if not LEGACY_DISABLED_DIR.exists() or not LEGACY_SRC_DIR.exists():
            return False
        try:
            legacy_root = str(LEGACY_DISABLED_DIR)
            if legacy_root not in sys.path:
                sys.path.insert(0, legacy_root)
            import importlib
            try:
                import gradio  # noqa: F401
            except Exception:
                import types
                dummy_gradio = types.ModuleType("gradio")
                class _DummyBlocks:
                    pass
                dummy_gradio.Blocks = _DummyBlocks
                sys.modules["gradio"] = dummy_gradio
            importlib.import_module("src.functional.sd_webui_prompt_all_in_one_app.sd_webui_prompt_all_in_one.scripts.on_app_started")
            importlib.import_module("src.functional.script.autocomplete")
            return True
        except Exception as exc:
            print(f"[PromptStudio] exact legacy backend import failed: {exc}")
            return False

    if _try_register_exact_legacy_backend():
        ROUTES_REGISTERED = True
        return
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
        return web.json_response({"info": "ok"})

    @routes.post("/weilin/physton_prompt/edit_group_tags")
    async def prompt_studio_edit_group_tags(request):
        return web.json_response({"info": "ok"})

    @routes.post("/weilin/physton_prompt/delete_group_tags")
    async def prompt_studio_delete_group_tags(request):
        return web.json_response({"info": "ok"})

    @routes.post("/weilin/physton_prompt/new_node_group_tags")
    async def prompt_studio_new_node_group_tags(request):
        return web.json_response({"info": "ok"})

    @routes.post("/weilin/physton_prompt/new_group_tags")
    async def prompt_studio_new_group_tags(request):
        return web.json_response({"info": "ok"})

    @routes.post("/weilin/physton_prompt/edit_node_group_tags")
    async def prompt_studio_edit_node_group_tags(request):
        return web.json_response({"info": "ok"})

    @routes.post("/weilin/physton_prompt/edit_child_group_tags")
    async def prompt_studio_edit_child_group_tags(request):
        return web.json_response({"info": "ok"})

    @routes.post("/weilin/physton_prompt/delete_node_group_tags")
    async def prompt_studio_delete_node_group_tags(request):
        return web.json_response({"info": "ok"})

    @routes.post("/weilin/physton_prompt/delete_child_group_tags")
    async def prompt_studio_delete_child_group_tags(request):
        return web.json_response({"info": "ok"})

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
        return web.json_response({"extra_networks": _build_extra_networks()})

    ROUTES_REGISTERED = True




