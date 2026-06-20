import argparse
import base64
import csv
import gc
import json
import mimetypes
import os
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
GATEWAY_BUILD_ID = str(Path(__file__).stat().st_mtime_ns)
FRONTEND_PATH = PROJECT_DIR / "frontend" / "index.html"
RUNTIME_DIR = PROJECT_DIR / "runtime"
RUNTIME_LOG_DIR = RUNTIME_DIR / "logs"
RUNTIME_TEMP_DIR = RUNTIME_DIR / "temp"
KOBOLDCPP_TEMP_ROOT = Path(
    os.environ.get("STUDIO_SUITE_KOBOLDCPP_TEMP_DIR")
    or (Path(tempfile.gettempdir()) / "ComfyUI-Studio-Suite" / "koboldcpp")
)
PROMPT_PROFILES_PATH = PROJECT_DIR / "config" / "prompt_profiles.json"
BACKEND_PROFILES_PATH = PROJECT_DIR / "config" / "backend_profiles.json"
CHARACTER_ALIASES_PATH = PROJECT_DIR / "resources" / "danbooru_character_aliases.json"
GENERATED_CHARACTER_ALIASES_PATH = PROJECT_DIR / "resources" / "danbooru_character_aliases.generated.json"
CHARACTER_ALIAS_SAFETY_PATH = PROJECT_DIR / "resources" / "character_alias_safety.json"
CHARACTER_WEBUI_NORMALIZED_PATH = PROJECT_DIR / "resources" / "danbooru_character_webui.normalized.jsonl"
TAG_COUNT_TAGS_JSONL_PATH = PROJECT_DIR / "resources" / "tag_count_tags_统计.jsonl"


def read_text_tail(path_value, max_chars=12000):
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.exists():
        return ""
    try:
        data = path.read_bytes()
    except Exception as error:
        return f"<failed to read log tail: {error}>"
    if len(data) > max_chars:
        data = data[-max_chars:]
    return data.decode("utf-8", errors="replace")


def runtime_temp_path(subdir):
    raw = str(subdir or "default").replace("\\", "/").strip("/")
    if raw == "koboldcpp":
        return KOBOLDCPP_TEMP_ROOT
    if raw.startswith("koboldcpp/"):
        return KOBOLDCPP_TEMP_ROOT / raw.split("/", 1)[1]
    return RUNTIME_TEMP_DIR / raw


def runtime_temp_env(subdir):
    temp_dir = runtime_temp_path(subdir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["TEMP"] = str(temp_dir)
    env["TMP"] = str(temp_dir)
    env["TMPDIR"] = str(temp_dir)
    return env


def make_runtime_temp_subdir(prefix):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return f"{prefix}/{stamp}_{os.getpid()}_{uuid.uuid4().hex[:8]}"


def cleanup_runtime_temp_subdir(subdir):
    temp_dir = runtime_temp_path(subdir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    cleaned = []
    failed = []
    for child in list(temp_dir.iterdir()):
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=False)
            else:
                child.unlink()
            cleaned.append(str(child))
        except Exception as error:
            failed.append({"path": str(child), "error": str(error)})
    return {"temp_dir": str(temp_dir), "cleaned_count": len(cleaned), "failed": failed[:8]}


def cleanup_runtime_temp_report(current_subdir=None):
    report = {
        "current": cleanup_runtime_temp_subdir(current_subdir) if current_subdir else None,
        "parent": cleanup_runtime_temp_subdir("koboldcpp"),
    }
    return report


def find_windows_pids_on_tcp_port(port):
    if os.name != "nt" or not port:
        return []
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except Exception:
        return []
    pids = set()
    pattern = re.compile(rf"^\s*TCP\s+\S+:{int(port)}\s+\S+\s+\S+\s+(\d+)\s*$", re.IGNORECASE)
    for line in (result.stdout or "").splitlines():
        match = pattern.match(line)
        if match:
            try:
                pids.add(int(match.group(1)))
            except ValueError:
                pass
    return sorted(pids)


def parse_port_from_url(url, default_port):
    match = re.search(r":(\d+)(?:/|$)", str(url or ""))
    if not match:
        return default_port
    try:
        return int(match.group(1))
    except ValueError:
        return default_port
TAG_COOCCURRENCE_CSV_PATH = PROJECT_DIR / "resources" / "danbooru_tags_cooccurrence.csv"
CLOTHING_JSONL_PATH = PROJECT_DIR / "resources" / "Danbooru服装查询资源_本地版_2026-05-26.jsonl"
CLOTHING_TEXT_PATH = PROJECT_DIR / "resources" / "Danbooru服装查询资源_层级版_2026-05-26.txt"
LEGACY_OUTFIT_SYSTEM_PATH = PROJECT_DIR / "resources" / "服装生成模板_system_legacy.md"
RUNTIME_DIR = PROJECT_DIR / "runtime"
PRIVATE_PYTHON_LIBS_DIR = RUNTIME_DIR / "python_libs"
PRIVATE_LLAMA_CPP_PYTHON_DIR = PRIVATE_PYTHON_LIBS_DIR / "llama_cpp_python_cu130"
INPROCESS_BACKEND_PROVIDERS = {"llama_cpp_python_inproc", "transformers_inproc", "clip_reuse"}
LEGACY_PROJECT_ROOTS = [
]

COOCCURRENCE_GENERIC_SEED_KEYS = {
    "1girl",
    "1boy",
    "solo",
    "looking at viewer",
    "simple background",
    "white background",
    "smile",
    "open mouth",
}

COOCCURRENCE_DEFAULT_RELATED_BLOCK_KEYS = {
    "breasts",
    "large breasts",
    "cleavage",
    "nipples",
    "nude",
    "naked",
    "pussy",
    "sex",
    "nsfw",
    "rating explicit",
    "explicit",
    "multiple girls",
    "multiple boys",
    "2girls",
    "3girls",
    "4girls",
    "5girls",
    "6+girls",
    "2boys",
    "3boys",
    "4boys",
    "5boys",
    "6+boys",
}

GENERIC_DESCRIPTOR_ALIAS_WORDS = {
    "1girl",
    "1boy",
    "solo",
    "smile",
    "grin",
    "frown",
    "blush",
    "black",
    "white",
    "blue",
    "red",
    "green",
    "grey",
    "gray",
    "aqua",
    "pink",
    "purple",
    "brown",
    "long",
    "short",
    "hair",
    "eyes",
    "shirt",
    "skirt",
    "dress",
    "boots",
    "shoes",
    "flower",
    "circle",
    "petals",
    "headphones",
    "ribbon",
    "bow",
    "holding",
    "looking",
    "viewer",
    "standing",
    "sitting",
    "full",
    "body",
}


def load_json_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_runtime_options(runtime_options):
    if not isinstance(runtime_options, dict):
        return {}
    allowed = {
        "llama_cpp_python_n_gpu_layers",
        "llama_cpp_python_threads",
        "llama_cpp_python_n_batch",
        "llama_cpp_python_n_ubatch",
        "llama_cpp_python_chat_format",
        "llama_cpp_python_verbose",
    }
    return {str(key): value for key, value in runtime_options.items() if str(key) in allowed}


def runtime_options_cache_key(runtime_options):
    normalized = normalize_runtime_options(runtime_options)
    if not normalized:
        return ""
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_jsonl_file(path):
    records = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def dump_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def load_prompt_profiles():
    if PROMPT_PROFILES_PATH.exists():
        return load_json_file(PROMPT_PROFILES_PATH)
    return {}


def load_backend_profiles():
    if BACKEND_PROFILES_PATH.exists():
        return load_json_file(BACKEND_PROFILES_PATH)
    return {}


def normalize_optional_path(text):
    value = str(text or "").strip()
    return value or None


def normalize_slashes(path_text):
    return str(path_text or "").replace("\\", "/")


def iter_resolved_path_candidates(path_value, *, project_subdirs=None, fallback_names=None):
    normalized = normalize_optional_path(path_value)
    if not normalized:
        return

    project_subdirs = list(project_subdirs or [])
    fallback_names = list(fallback_names or [])

    seen = set()

    def push(candidate):
        try:
            key = str(Path(candidate))
        except Exception:
            key = str(candidate)
        if key in seen:
            return
        seen.add(key)
        yield Path(candidate)

    raw_path = Path(normalized)
    if raw_path.is_absolute():
        yield from push(raw_path)
    else:
        yield from push(PROJECT_DIR / normalized)
        for subdir in project_subdirs:
            yield from push(PROJECT_DIR / subdir / normalized)

    normalized_forward = normalize_slashes(normalized)
    for legacy_root in LEGACY_PROJECT_ROOTS:
        legacy_prefix = normalize_slashes(legacy_root).rstrip("/") + "/"
        if normalized_forward.lower().startswith(legacy_prefix.lower()):
            relative_suffix = normalized_forward[len(legacy_prefix) :]
            if relative_suffix:
                yield from push(PROJECT_DIR / relative_suffix)

    basename = Path(normalized_forward).name
    if basename:
        for subdir in project_subdirs:
            yield from push(PROJECT_DIR / subdir / basename)
        for name in fallback_names:
            yield from push(PROJECT_DIR / name)


def resolve_existing_path(path_value, *, purpose, required=True, project_subdirs=None, fallback_names=None):
    candidates = list(
        iter_resolved_path_candidates(
            path_value,
            project_subdirs=project_subdirs,
            fallback_names=fallback_names,
        )
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    if not required:
        return None
    searched = [str(candidate) for candidate in candidates]
    raise FileNotFoundError(
        f"Unable to resolve {purpose}: {path_value!r}. Searched: {searched or ['<no candidates>']}"
    )


def resolve_koboldcpp_executable(path_value):
    env_override = normalize_optional_path(os.getenv("TASK_AGENT_KOBOLDCPP_EXE"))
    if env_override:
        resolved = resolve_existing_path(
            env_override,
            purpose="koboldcpp executable from TASK_AGENT_KOBOLDCPP_EXE",
            required=False,
            project_subdirs=["runtime", "runtime/koboldcpp", "koboldcpp", "third_party/koboldcpp"],
        )
        if resolved:
            return resolved

    resolved = resolve_existing_path(
        path_value,
        purpose="koboldcpp executable",
        required=False,
        project_subdirs=["runtime", "runtime/koboldcpp", "koboldcpp", "third_party/koboldcpp"],
        fallback_names=[
            "runtime/koboldcpp/koboldcpp.exe",
            "koboldcpp/koboldcpp.exe",
            "third_party/koboldcpp/koboldcpp.exe",
        ],
    )
    if resolved:
        return resolved

    raise FileNotFoundError(
        "Unable to locate koboldcpp.exe. Set backend.koboldcpp_exe, or place it under "
        f"{PROJECT_DIR / 'runtime' / 'koboldcpp'}"
    )


def resolve_llama_cpp_server_executable(path_value):
    env_override = normalize_optional_path(os.getenv("TASK_AGENT_LLAMA_CPP_SERVER_EXE"))
    if env_override:
        resolved = resolve_existing_path(
            env_override,
            purpose="llama.cpp server executable from TASK_AGENT_LLAMA_CPP_SERVER_EXE",
            required=False,
            project_subdirs=["runtime", "runtime/llama.cpp", "llama.cpp", "third_party/llama.cpp"],
        )
        if resolved:
            return resolved

    resolved = resolve_existing_path(
        path_value,
        purpose="llama.cpp server executable",
        required=False,
        project_subdirs=["runtime", "runtime/llama.cpp", "llama.cpp", "third_party/llama.cpp"],
        fallback_names=[
            "runtime/llama.cpp/llama-server.exe",
            "runtime/llama.cpp/llama-server",
            "llama.cpp/llama-server.exe",
            "llama.cpp/llama-server",
            "third_party/llama.cpp/llama-server.exe",
            "third_party/llama.cpp/llama-server",
        ],
    )
    if resolved:
        return resolved

    raise FileNotFoundError(
        "Unable to locate llama.cpp server executable. Set backend.llama_cpp_server_exe, or place it under "
        f"{PROJECT_DIR / 'runtime' / 'llama.cpp'}"
    )


def parse_extra_launch_args(args_text):
    text = str(args_text or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text, posix=False if os.name == "nt" else True)
    except ValueError as error:
        raise ValueError(f"Invalid backend extra args: {error}") from error


def message_content_to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        unsupported_types = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "text":
                text_parts.append(str(item.get("text", "")))
            else:
                unsupported_types.append(str(item_type or "unknown"))
        if unsupported_types:
            raise ValueError(
                "Current in-process text runtime does not support non-text message parts: "
                + ", ".join(sorted(set(unsupported_types)))
            )
        return "\n".join(part for part in text_parts if part)
    return str(content or "")


def normalize_messages_for_text_runtime(messages):
    return [
        {
            "role": message.get("role", "user"),
            "content": message_content_to_text(message.get("content", "")),
        }
        for message in messages
    ]


def messages_have_image_content(messages):
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image_url":
                return True
    return False


def ensure_private_python_lib_path(path):
    path_obj = Path(path)
    if not path_obj.exists():
        return False
    path_text = str(path_obj)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)
    return True


def add_dll_directory_if_exists(path):
    path_obj = Path(path)
    if os.name != "nt" or not path_obj.exists() or not hasattr(os, "add_dll_directory"):
        return False
    os.add_dll_directory(str(path_obj))
    return True


def prepare_llama_cpp_dll_paths(private_root):
    root = Path(private_root)
    add_dll_directory_if_exists(root / "llama_cpp" / "lib")
    try:
        import torch

        add_dll_directory_if_exists(Path(torch.__file__).resolve().parent / "lib")
    except Exception:
        pass


def load_llama_cpp_for_task_agent(prefer_private=True):
    if prefer_private:
        ensure_private_python_lib_path(PRIVATE_LLAMA_CPP_PYTHON_DIR)
        prepare_llama_cpp_dll_paths(PRIVATE_LLAMA_CPP_PYTHON_DIR)
    try:
        import llama_cpp
        from llama_cpp import Llama
    except Exception as error:
        raise RuntimeError(
            "llama-cpp-python is not available. Install it globally or place a private build under "
            f"{PRIVATE_LLAMA_CPP_PYTHON_DIR}"
        ) from error

    module_path = str(Path(getattr(llama_cpp, "__file__", "") or ""))
    version = str(getattr(llama_cpp, "__version__", "") or "")
    if prefer_private and PRIVATE_LLAMA_CPP_PYTHON_DIR.exists() and str(PRIVATE_LLAMA_CPP_PYTHON_DIR) not in module_path:
        raise RuntimeError(
            "llama_cpp was already imported from another location, so Task Agent cannot use its private build. "
            f"loaded={module_path}, private={PRIVATE_LLAMA_CPP_PYTHON_DIR}. Restart ComfyUI before using "
            "llama_cpp_python_inproc, or disable nodes that import llama_cpp first."
        )
    return Llama, {"version": version, "module_path": module_path}


def build_llama_cpp_multimodal_chat_handler(runtime_spec, runtime_options, verbose=False):
    mmproj_path = str(runtime_spec.get("mmproj_path") or "").strip()
    if not mmproj_path:
        return None
    try:
        from llama_cpp.llama_chat_format import Gemma4ChatHandler, MTMDChatHandler
    except Exception as error:
        raise RuntimeError("llama-cpp-python multimodal chat handlers are not available.") from error

    requested = str(runtime_options.get("llama_cpp_python_chat_format", "") or "").strip().lower()
    profile_name = str(runtime_spec.get("profile_name", "") or "").lower()
    model_name = str(runtime_spec.get("effective_name", "") or "").lower()
    handler_cls = Gemma4ChatHandler if requested == "gemma4" or "gemma4" in profile_name or "gemma4" in model_name else MTMDChatHandler
    return handler_cls(clip_model_path=mmproj_path, verbose=bool(verbose))


def cuda_free_mb_from_snapshot(memory_snapshot):
    cuda = {}
    if isinstance(memory_snapshot, dict):
        cuda = memory_snapshot.get("cuda", {}) if isinstance(memory_snapshot.get("cuda", {}), dict) else {}
    return int(cuda.get("free_mb") or 0)


def free_comfy_vram_for_inprocess_llm(target_free_vram_mb=0, attempts=3, wait_seconds=0.25):
    """Best-effort cleanup before loading llama.cpp inside the ComfyUI process."""
    target_free_vram_mb = max(0, int(target_free_vram_mb or 0))
    attempts = max(1, int(attempts or 1))
    result = {
        "attempted": True,
        "target_free_vram_mb": target_free_vram_mb,
        "attempts": [],
        "target_reached": False,
    }
    for attempt_index in range(attempts):
        pass_result = {"attempt": attempt_index + 1, "comfy_unload": "not_available", "torch_cache": "not_available"}
        pass_result["memory_before"] = get_combined_memory_snapshot()
        try:
            import comfy.model_management as model_management

            current_loaded_models = getattr(model_management, "current_loaded_models", None)
            if isinstance(current_loaded_models, list):
                pass_result["comfy_loaded_models_before"] = len(current_loaded_models)
            unload_all = getattr(model_management, "unload_all_models", None)
            cleanup_models_gc = getattr(model_management, "cleanup_models_gc", None)
            soft_empty_cache = getattr(model_management, "soft_empty_cache", None)
            if callable(unload_all):
                unload_all()
                pass_result["comfy_unload"] = "ok"
            if callable(cleanup_models_gc):
                cleanup_models_gc()
                pass_result["comfy_cleanup_models_gc"] = "ok"
            if callable(soft_empty_cache):
                try:
                    soft_empty_cache(force=True)
                except TypeError:
                    soft_empty_cache()
                pass_result["comfy_soft_empty_cache"] = "ok"
            if isinstance(current_loaded_models, list):
                pass_result["comfy_loaded_models_after"] = len(current_loaded_models)
        except Exception as error:
            pass_result["comfy_unload"] = f"failed: {error}"

        gc.collect()

        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
                torch.cuda.synchronize()
                pass_result["torch_cache"] = "ok"
            else:
                pass_result["torch_cache"] = "cuda_unavailable"
        except Exception as error:
            pass_result["torch_cache"] = f"failed: {error}"

        pass_result["memory_after"] = get_combined_memory_snapshot()
        result["attempts"].append(pass_result)
        free_mb = cuda_free_mb_from_snapshot(pass_result["memory_after"])
        if target_free_vram_mb <= 0 or free_mb >= target_free_vram_mb:
            result["target_reached"] = True
            break
        if attempt_index < attempts - 1:
            time.sleep(float(wait_seconds or 0))

    result["final_memory"] = get_combined_memory_snapshot()
    result["final_free_vram_mb"] = cuda_free_mb_from_snapshot(result["final_memory"])
    return result


def get_cuda_memory_snapshot():
    try:
        import torch

        if not torch.cuda.is_available():
            return {"available": False}
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        return {
            "available": True,
            "free_mb": int(free_bytes // (1024 * 1024)),
            "total_mb": int(total_bytes // (1024 * 1024)),
            "allocated_mb": int(torch.cuda.memory_allocated() // (1024 * 1024)),
            "reserved_mb": int(torch.cuda.memory_reserved() // (1024 * 1024)),
        }
    except Exception as error:
        return {"available": False, "error": str(error)}


def get_system_memory_snapshot():
    if os.name != "nt":
        return {"available": False, "reason": "unsupported_platform"}
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return {"available": False, "error": "GlobalMemoryStatusEx failed"}
        return {
            "available": True,
            "memory_load_percent": int(status.dwMemoryLoad),
            "total_phys_mb": int(status.ullTotalPhys // (1024 * 1024)),
            "avail_phys_mb": int(status.ullAvailPhys // (1024 * 1024)),
            "total_pagefile_mb": int(status.ullTotalPageFile // (1024 * 1024)),
            "avail_pagefile_mb": int(status.ullAvailPageFile // (1024 * 1024)),
        }
    except Exception as error:
        return {"available": False, "error": str(error)}


def get_process_memory_snapshot():
    try:
        import psutil

        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        return {
            "available": True,
            "source": "psutil",
            "rss_mb": int(memory_info.rss // (1024 * 1024)),
            "vms_mb": int(memory_info.vms // (1024 * 1024)),
            "private_mb": int(getattr(memory_info, "private", 0) // (1024 * 1024)),
        }
    except Exception:
        pass

    if os.name != "nt":
        return {"available": False, "reason": "unsupported_platform"}
    try:
        import ctypes

        class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return {"available": False, "error": "GetProcessMemoryInfo failed"}
        return {
            "available": True,
            "working_set_mb": int(counters.WorkingSetSize // (1024 * 1024)),
            "peak_working_set_mb": int(counters.PeakWorkingSetSize // (1024 * 1024)),
            "pagefile_usage_mb": int(counters.PagefileUsage // (1024 * 1024)),
            "peak_pagefile_usage_mb": int(counters.PeakPagefileUsage // (1024 * 1024)),
            "private_usage_mb": int(counters.PrivateUsage // (1024 * 1024)),
        }
    except Exception as error:
        return {"available": False, "error": str(error)}


def get_combined_memory_snapshot():
    return {
        "cuda": get_cuda_memory_snapshot(),
        "system": get_system_memory_snapshot(),
        "process": get_process_memory_snapshot(),
    }


def trim_process_working_set():
    if os.name != "nt":
        return {"attempted": False, "reason": "unsupported_platform"}
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if hasattr(ctypes.windll, "psapi") and ctypes.windll.psapi.EmptyWorkingSet(handle):
            return {"attempted": True, "status": "ok", "method": "EmptyWorkingSet"}
        set_working_set_size = ctypes.windll.kernel32.SetProcessWorkingSetSize
        set_working_set_size.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
        set_working_set_size.restype = ctypes.c_bool
        max_size_t = ctypes.c_size_t(-1).value
        if set_working_set_size(handle, max_size_t, max_size_t):
            return {"attempted": True, "status": "ok", "method": "SetProcessWorkingSetSize"}
        return {"attempted": True, "status": "failed"}
    except Exception as error:
        return {"attempted": True, "status": "failed", "error": str(error)}


def choose_safe_llama_cpp_gpu_layers(requested_layers, memory_snapshot, max_gpu_layers=8):
    try:
        requested = int(requested_layers)
    except Exception:
        requested = -1
    try:
        max_gpu_layers = max(0, int(max_gpu_layers))
    except Exception:
        max_gpu_layers = 8
    if requested == 0:
        return 0, {"requested": requested, "effective": 0, "reason": "cpu_only_requested"}

    cuda = {}
    if isinstance(memory_snapshot, dict):
        cuda = memory_snapshot.get("cuda", {}) if isinstance(memory_snapshot.get("cuda", {}), dict) else {}
    free_mb = int(cuda.get("free_mb") or 0)
    if free_mb <= 0:
        return requested, {"requested": requested, "effective": requested, "reason": "unknown_free_vram"}

    # Comfy workflows usually need to load the image model immediately after the
    # LLM task. Keep a larger VRAM floor than llama.cpp itself would require.
    if free_mb < 3500:
        cap = 0
    elif free_mb < 5000:
        cap = 4
    elif free_mb < 6500:
        cap = 8
    else:
        cap = max_gpu_layers or (requested if requested > 0 else 8)

    effective = min(requested, cap) if requested > 0 else cap
    return effective, {
        "requested": requested,
        "effective": effective,
        "free_vram_mb": free_mb,
        "cap": cap,
        "max_gpu_layers": max_gpu_layers,
        "reason": "vram_safety_cap" if effective != requested else "unchanged",
    }


def choose_stable_llama_cpp_ubatch(n_batch, context_size, runtime_options, backend):
    explicit = runtime_options.get("llama_cpp_python_n_ubatch", backend.get("llama_cpp_python_n_ubatch", ""))
    if str(explicit or "").strip():
        return max(16, int(explicit))
    batch = max(16, int(n_batch or 512))
    ctx = int(context_size or 4096)
    if ctx >= 8192:
        return min(batch, 64)
    return min(batch, 256)


def minimum_safe_post_llama_load_vram_mb(runtime_spec, runtime_options, backend):
    explicit = runtime_options.get(
        "llama_cpp_python_min_free_vram_after_load_mb",
        backend.get("llama_cpp_python_min_free_vram_after_load_mb", ""),
    )
    if str(explicit or "").strip():
        return max(0, int(explicit))
    if (runtime_spec or {}).get("mmproj_path"):
        return 1400
    return 1000


def minimum_safe_pre_llama_load_vram_mb(requested_layers, runtime_spec, runtime_options, backend):
    explicit = runtime_options.get(
        "llama_cpp_python_min_free_vram_before_load_mb",
        backend.get("llama_cpp_python_min_free_vram_before_load_mb", ""),
    )
    if str(explicit or "").strip():
        return max(0, int(explicit))
    try:
        requested = int(requested_layers)
    except Exception:
        requested = -1
    if requested == 0:
        return 0
    if (runtime_spec or {}).get("mmproj_path"):
        return 3500
    return 3000


def normalize_alias_key(text):
    return (
        str(text)
        .strip()
        .lower()
        .replace("\\(", "(")
        .replace("\\)", ")")
        .replace("\\", "")
        .replace("_", " ")
        .replace("-", " ")
    )


def contains_cjk(text):
    return any("\u4e00" <= char <= "\u9fff" for char in str(text))


def load_character_alias_safety():
    default = {
        "blocked_canonical_tags": [
            "blobcat",
            "grape-flavored_blobcat",
        ],
        "blocked_aliases": [
            "blobcat",
            "grape",
            "grape-flavored blobcat",
            "grape-flavored_blobcat",
            "danbooru",
            "danbooru (site)",
            "kemono friends",
            "indie virtual youtuber",
        ],
        "blocked_generic_noun_aliases": [
            "hood",
            "jewelry",
            "coat",
            "shirt",
            "dress",
            "skirt",
            "jacket",
            "blouse",
            "glasses",
            "headphones",
            "hairpin",
            "ribbon",
            "bracelet",
            "necklace",
            "earrings",
            "boots",
            "shoes",
            "hat",
        ],
        "suspicious_copyright_tags": [
            "original",
            "danbooru_(site)",
            "indie_virtual_youtuber",
        ],
        "single_word_ascii_min_count": 50,
    }
    if not CHARACTER_ALIAS_SAFETY_PATH.exists():
        return default
    try:
        loaded = load_json_file(CHARACTER_ALIAS_SAFETY_PATH)
    except Exception:
        return default
    merged = dict(default)
    merged.update(loaded or {})
    return merged


CHARACTER_ALIAS_SAFETY = load_character_alias_safety()


def is_single_ascii_word(text):
    normalized = normalize_alias_key(text)
    return bool(normalized) and normalized.isascii() and len(normalized.split()) == 1


def is_generic_descriptor_alias(text):
    normalized = normalize_alias_key(text)
    if not normalized:
        return False
    words = [item for item in normalized.split() if item]
    if not words or len(words) > 3:
        return False
    return all(word in GENERIC_DESCRIPTOR_ALIAS_WORDS for word in words)


def should_skip_character_alias(alias_clean, canonical_clean, metadata, mode):
    alias_key = normalize_alias_key(alias_clean)
    canonical_key = normalize_alias_key(canonical_clean)
    if not alias_key or not canonical_key:
        return True

    blocked_aliases = {normalize_alias_key(item) for item in CHARACTER_ALIAS_SAFETY.get("blocked_aliases", [])}
    blocked_generic_noun_aliases = {
        normalize_alias_key(item) for item in CHARACTER_ALIAS_SAFETY.get("blocked_generic_noun_aliases", [])
    }
    blocked_canonicals = {normalize_alias_key(item) for item in CHARACTER_ALIAS_SAFETY.get("blocked_canonical_tags", [])}
    suspicious_copyrights = {
        normalize_alias_key(item) for item in CHARACTER_ALIAS_SAFETY.get("suspicious_copyright_tags", [])
    }

    if alias_key in blocked_aliases or canonical_key in blocked_canonicals:
        return True
    if alias_key in blocked_generic_noun_aliases:
        return True

    copyright_tag_key = normalize_alias_key(metadata.get("copyright_tag", ""))
    copyright_name_key = normalize_alias_key(metadata.get("copyright_name_zh", ""))
    if alias_key and (alias_key == copyright_tag_key or alias_key == copyright_name_key):
        return True

    count = int(metadata.get("count") or 0)
    source_name = str(metadata.get("_source", "")).strip().lower()
    if source_name != "manual" and is_generic_descriptor_alias(alias_clean):
        return True
    if is_single_ascii_word(alias_clean):
        if mode == "tag_text" and source_name != "manual":
            return True
        if mode == "free_text" and count < int(CHARACTER_ALIAS_SAFETY.get("single_word_ascii_min_count", 50)):
            return True
        if copyright_tag_key in suspicious_copyrights and count < 1000:
            return True

    return False


def load_character_aliases():
    aliases = {}
    resource_paths = [GENERATED_CHARACTER_ALIASES_PATH, CHARACTER_ALIASES_PATH]
    for resource_path in resource_paths:
        if not resource_path.exists():
            continue
        raw = load_json_file(resource_path)
        source_name = "manual" if resource_path == CHARACTER_ALIASES_PATH else "generated"
        for canonical_tag, alias_spec in raw.items():
            canonical_clean = str(canonical_tag).strip()
            if not canonical_clean:
                continue
            if isinstance(alias_spec, dict):
                alias_list = alias_spec.get("aliases", [])
                blocked_tags = alias_spec.get("blocked_tags", [])
                metadata = dict(alias_spec.get("metadata", {}) or {})
            else:
                alias_list = alias_spec
                blocked_tags = []
                metadata = {}
            metadata["_source"] = source_name
            if should_skip_character_alias(canonical_clean, canonical_clean, metadata, mode="load"):
                continue
            if resource_path == CHARACTER_ALIASES_PATH:
                priority = 10**12
            else:
                priority = int(metadata.get("count") or 0)
            canonical_key = normalize_alias_key(canonical_clean)
            current = aliases.get(canonical_key)
            entry = {
                "canonical_tag": canonical_clean,
                "matched_alias": canonical_clean,
                "blocked_tags": [str(tag).strip() for tag in blocked_tags if str(tag).strip()],
                "_priority": priority,
                "_metadata": dict(metadata or {}),
            }
            if current is None or priority >= current.get("_priority", -1):
                aliases[canonical_key] = entry
            for alias in alias_list:
                alias_clean = str(alias).strip()
                if not alias_clean:
                    continue
                if should_skip_character_alias(alias_clean, canonical_clean, metadata, mode="load"):
                    continue
                alias_key = normalize_alias_key(alias_clean)
                current = aliases.get(alias_key)
                entry = {
                    "canonical_tag": canonical_clean,
                    "matched_alias": alias_clean,
                    "blocked_tags": [str(tag).strip() for tag in blocked_tags if str(tag).strip()],
                    "_priority": priority,
                    "_metadata": dict(metadata or {}),
                }
                if current is None or priority >= current.get("_priority", -1):
                    aliases[alias_key] = entry
    return aliases


CHARACTER_ALIASES = load_character_aliases()


def load_clothing_records():
    if not CLOTHING_JSONL_PATH.exists():
        return []
    return load_jsonl_file(CLOTHING_JSONL_PATH)


CLOTHING_RECORDS = load_clothing_records()
CLOTHING_RECORDS_BY_TAG = {
    str(item.get("tag", "")).strip().lower(): item
    for item in CLOTHING_RECORDS
    if str(item.get("tag", "")).strip()
}


def load_legacy_outfit_system_digest():
    if not LEGACY_OUTFIT_SYSTEM_PATH.exists():
        return ""

    text = LEGACY_OUTFIT_SYSTEM_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    selected = []
    capture = False
    skip_subsection = False
    allowed_sections = {
        "# 中文→Danbooru 二次元翻译指南（重要！）",
        "# 固定设定（不可更改）",
        "# 服装设计指导",
        "# 输出要求",
        "# 约束",
    }
    ignored_subsection_prefixes = (
        "## 风格",
        "## prompt",
        "## chibi_prompt",
        "## music_prompt",
        "## 参考标签组合",
    )
    blocked_line_fragments = (
        "上身优先有叠穿层次",
        "上身叠穿",
        "最好至少两层",
        "内搭+外层",
        "外套+T恤叠穿",
        "衬衫/针织/短外套叠穿",
        "知性衬衫/修身上衣+外套叠穿",
        "轻薄上衣+衬衫或背心叠穿",
        "条纹长袖+黑色T恤叠穿",
    )
    for line in lines:
        if line.startswith("# "):
            capture = line.strip() in allowed_sections
            skip_subsection = False
            if capture:
                selected.append(line.strip())
            continue
        if not capture:
            continue
        stripped = line.rstrip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            skip_subsection = stripped.startswith(ignored_subsection_prefixes)
            continue
        if skip_subsection:
            continue
        if stripped.startswith("参考标签组合"):
            continue
        if any(fragment in stripped for fragment in blocked_line_fragments):
            continue
        if stripped.startswith("- ") or stripped.startswith("**") or stripped.startswith("1.") or stripped.startswith("2.") or stripped.startswith("3.") or stripped.startswith("4.") or stripped.startswith("5.") or stripped.startswith("6.") or stripped.startswith("7.") or stripped.startswith("8.") or stripped.startswith("9.") or stripped.startswith("10.") or stripped.startswith("11.") or stripped.startswith("12.") or stripped.startswith("13.") or stripped.startswith("14.") or stripped.startswith("15.") or stripped.startswith("16.") or stripped.startswith("17."):
            selected.append(stripped)
    return "\n".join(selected[:180])


LEGACY_OUTFIT_SYSTEM_DIGEST = load_legacy_outfit_system_digest()


GENERIC_DAILY_BLOCKED_TAGS = {
    "aiguillette",
    "animal_hat",
    "bandeau",
    "bathrobe",
    "bicorne",
    "bodysuit",
    "bodystocking",
    "boqtaq",
    "cat_cutout",
    "cape",
    "capelet",
    "cardigan_shrug",
    "chest_harness",
    "chest_strap",
    "classic_lolita",
    "cleavage_cutout",
    "clothes_around_waist",
    "corset",
    "crown",
    "cutout",
    "deerstalker",
    "football_helmet",
    "forehead_protector",
    "habit",
    "hardhat",
    "harness",
    "head_mirror",
    "helmet",
    "hip_shawl",
    "hip_vent",
    "hoshi_no_kanmuri",
    "inrou",
    "kabuto",
    "labcoat",
    "leotard",
    "maid_apron",
    "mini_crown",
    "native_american_headdress",
    "naked_sweater",
    "nurse_cap",
    "open_robe",
    "pants",
    "pectoral",
    "pirate_hat",
    "pith_helmet",
    "pocket_square",
    "pumpkin_hat",
    "robe",
    "sarong",
    "see_through",
    "shako_cap",
    "shimenawa",
    "surcoat",
    "tabard",
    "taut_shirt",
    "toque_blanche",
    "top",
    "trench_coat",
    "tubetop",
    "underbust",
    "virgin_killer_sweater",
    "yaopei",
}

GENERIC_DAILY_BLOCKED_SUBSTRINGS = (
    "headdress",
    "helmet",
    "battle",
    "armor",
    "military",
)

SAFE_DAILY_TAG_BOOSTS = {
    "top": {"shirt", "blouse", "sweater", "pullover", "hoodie", "top"},
    "outer": {"cardigan", "jacket", "hooded_jacket", "blazer"},
    "onepiece": {"dress", "jumper_dress", "pinafore_dress", "sweater_dress"},
    "bottom": {"pleated_skirt", "long_skirt", "frilled_skirt", "jeans", "trousers", "capris", "shorts"},
    "footwear": {"loafers", "sneakers", "ankle_boots", "mary_janes"},
    "legwear": {"socks", "thighhighs", "tights"},
    "accessory": {"hair_ribbon", "hair_bow", "headband", "hair_pin", "hairband"},
    "detail": {"open_collar", "sleeves_pushed_up", "neck_ribbon", "bowtie", "choker", "pendant", "belt", "waist_sash", "ribbon_trim"},
}

DAILY_FALLBACK_TAGS = {
    "style": ["casual", "streetwear"],
    "top": ["shirt", "blouse", "sweater", "pullover", "hoodie", "vest", "sweater_vest"],
    "outer": ["cardigan", "jacket", "blazer", "hooded_jacket"],
    "onepiece": ["dress", "jumper_dress", "pinafore_dress", "sweater_dress"],
    "bottom": ["long_skirt", "frilled_skirt", "pleated_skirt", "jeans", "trousers", "capris", "shorts"],
    "legwear": ["socks"],
    "footwear": ["loafers", "sneakers", "ankle_boots"],
    "accessory": ["hair_ribbon", "hair_bow", "hair_pin", "headband"],
    "detail": ["open_collar", "sleeves_pushed_up", "neck_ribbon", "bowtie", "choker", "pendant", "belt", "waist_sash", "ribbon_trim"],
}

QUALITY_CONTROL_EXACT_TAGS = {
    "masterpiece",
    "best quality",
    "absurdres",
    "highres",
    "very awa",
    "high resolution",
    "aesthetic",
    "excellent",
    "newest",
    "uncensored",
    "safe",
    "nsfw",
    "rating explicit",
    "rating_explicit",
    "score_1",
    "score_2",
    "score_3",
    "score_7",
    "year 2025",
    "year_2025",
}

GLOBAL_BLOCKED_TAG_KEYS = {
    "blobcat",
    "grape flavored blobcat",
    "grape flavored_blobcat",
    "grape-flavored blobcat",
    "grape-flavored_blobcat",
}

GENERIC_DESCRIPTOR_ALIAS_WORDS = {
    "1girl",
    "1boy",
    "solo",
    "smile",
    "grin",
    "frown",
    "blush",
    "black",
    "white",
    "blue",
    "red",
    "green",
    "grey",
    "gray",
    "aqua",
    "pink",
    "purple",
    "brown",
    "long",
    "short",
    "hair",
    "eyes",
    "shirt",
    "skirt",
    "dress",
    "boots",
    "shoes",
    "flower",
    "circle",
    "petals",
    "headphones",
    "ribbon",
    "bow",
    "holding",
    "looking",
    "viewer",
    "standing",
    "sitting",
    "full",
    "body",
}


def http_get_json(url, timeout=15):
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def http_post_json(url, payload, timeout=600):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_context_bundle_json(text):
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {"bundle_raw_text": raw}
    if isinstance(parsed, dict):
        return parsed
    return {"bundle_value": parsed}


def guess_mime_type(path_text):
    guessed, _ = mimetypes.guess_type(str(path_text or "").strip())
    return guessed or "image/png"


def build_data_url_for_image_path(image_path):
    path_text = str(image_path or "").strip()
    if not path_text:
        return ""
    path = Path(path_text)
    if not path.exists() or not path.is_file():
        return ""
    mime_type = guess_mime_type(path)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def parse_resource_modules_from_inputs(inputs):
    context_bundle = parse_context_bundle_json(inputs.get("context_bundle_json", ""))
    modules = context_bundle.get("resource_modules", [])
    if not isinstance(modules, list):
        return []
    return [item for item in modules if isinstance(item, dict)]


def input_requests_resource_role(inputs, *roles):
    requested = {str(role).strip() for role in roles if str(role).strip()}
    if not requested:
        return False
    for item in parse_resource_modules_from_inputs(inputs):
        if str(item.get("resource_role", "")).strip() in requested:
            return True
    return False


def compact_text(text, limit=320):
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def normalize_lookup_tag(text):
    return normalize_alias_key(str(text or "").replace("\\(", "(").replace("\\)", ")"))


def build_user_message_content(task_type, user_prompt, inputs):
    image_path = str(inputs.get("image_path", "")).strip()
    visual_tasks = {
        "extract_tags_from_image",
        "vision_tagging",
        "image_captioning",
        "refine_wd14_tags",
        "generate_natural_caption",
    }
    if not image_path or str(task_type or "").strip() not in visual_tasks:
        return user_prompt
    if not bool(inputs.get("enable_image_input", False)):
        return user_prompt

    data_url = build_data_url_for_image_path(image_path)
    if not data_url:
        return user_prompt

    return [
        {"type": "text", "text": user_prompt},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]


def lookup_tag_statistics_for_tags(tags, limit=14):
    if not TAG_COUNT_TAGS_JSONL_PATH.exists():
        return []

    wanted = {normalize_lookup_tag(tag) for tag in tags if normalize_lookup_tag(tag)}
    if not wanted:
        return []

    hits = []
    seen = set()
    with open(TAG_COUNT_TAGS_JSONL_PATH, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            tag_name = str(record.get("Tag Name", "") or record.get("tag_name", "")).strip()
            if not tag_name:
                continue
            normalized = normalize_lookup_tag(tag_name)
            if normalized not in wanted or normalized in seen:
                continue
            seen.add(normalized)
            try:
                count = int(record.get("Count", 0) or record.get("count", 0) or 0)
            except Exception:
                count = 0
            hits.append({"tag": tag_name, "count": count})
            if len(hits) >= limit:
                break
    hits.sort(key=lambda item: int(item.get("count", 0)), reverse=True)
    return hits


def lookup_tag_cooccurrence_for_tags(tags, per_seed_limit=8, total_limit=28):
    if not TAG_COOCCURRENCE_CSV_PATH.exists():
        return []

    wanted = {
        normalize_lookup_tag(tag)
        for tag in tags
        if normalize_lookup_tag(tag) and normalize_lookup_tag(tag) not in COOCCURRENCE_GENERIC_SEED_KEYS
    }
    if not wanted:
        return []

    by_seed = {tag: [] for tag in wanted}
    seen_pairs = set()
    try:
        with open(TAG_COOCCURRENCE_CSV_PATH, "r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                tag_a = str(row.get("tag_a", "")).strip()
                tag_b = str(row.get("tag_b", "")).strip()
                if not tag_a or not tag_b:
                    continue
                norm_a = normalize_lookup_tag(tag_a)
                norm_b = normalize_lookup_tag(tag_b)
                matched = []
                if norm_a in wanted:
                    matched.append((norm_a, tag_b))
                if norm_b in wanted:
                    matched.append((norm_b, tag_a))
                if not matched:
                    continue
                try:
                    count = int(float(row.get("count", 0) or 0))
                except Exception:
                    count = 0
                for seed_norm, related_tag in matched:
                    related_norm = normalize_lookup_tag(related_tag)
                    if related_norm in COOCCURRENCE_DEFAULT_RELATED_BLOCK_KEYS:
                        continue
                    if len(by_seed.get(seed_norm, [])) >= per_seed_limit:
                        continue
                    pair_key = (seed_norm, related_norm)
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    by_seed[seed_norm].append(
                        {
                            "seed": seed_norm,
                            "related_tag": related_tag,
                            "count": count,
                        }
                    )
                if all(len(items) >= per_seed_limit for items in by_seed.values()):
                    break
    except Exception:
        return []

    merged = []
    for items in by_seed.values():
        merged.extend(items)
    merged.sort(key=lambda item: int(item.get("count", 0)), reverse=True)
    return merged[:total_limit]


def dictionary_suggested_tags_for_inputs(inputs, limit=18):
    if not input_requests_resource_role(inputs, "danbooru_cooccurrence"):
        return []
    raw_tag_candidates = split_tag_like_text(inputs.get("raw_tags", "") or inputs.get("raw_text", ""))
    hits = lookup_tag_cooccurrence_for_tags(raw_tag_candidates[:32], total_limit=limit)
    return dedupe_tags([item.get("related_tag", "") for item in hits if str(item.get("related_tag", "")).strip()])


def lookup_character_reference_entries(canonical_tags, limit=8):
    if not CHARACTER_WEBUI_NORMALIZED_PATH.exists():
        return []

    wanted = {normalize_lookup_tag(tag) for tag in canonical_tags if normalize_lookup_tag(tag)}
    if not wanted:
        return []

    hits = []
    with open(CHARACTER_WEBUI_NORMALIZED_PATH, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            canonical = str(record.get("character角色", "")).strip()
            if normalize_lookup_tag(canonical) not in wanted:
                continue
            try:
                count = int(record.get("count", 0) or 0)
            except Exception:
                count = 0
            hits.append(
                {
                    "canonical_tag": canonical,
                    "display_name_zh": str(record.get("character角色_2", "")).strip(),
                    "copyright_tag": str(record.get("copyright作品", "")).strip(),
                    "copyright_name_zh": str(record.get("copyright作品翻译2024.12.1", "")).strip(),
                    "trigger_tag": str(record.get("trigger触发tag_2", "") or record.get("trigger触发tag", "")).strip(),
                    "core_tags": str(record.get("core_tags_2", "") or record.get("core_tags", "")).strip(),
                    "count": count,
                }
            )
            if len(hits) >= limit:
                break
    hits.sort(key=lambda item: int(item.get("count", 0)), reverse=True)
    return hits


def build_local_dictionary_reference_text(inputs):
    parts = []
    alias_hits = inputs.get("resolved_character_tags", [])
    if alias_hits:
        parts.append("[Resolved Character Tags]")
        for item in alias_hits[:8]:
            parts.append(
                f"- canonical={item.get('canonical_tag', '')}, matched_alias={item.get('matched_alias', '')}"
            )

    canonical_tags = [item.get("canonical_tag", "") for item in alias_hits if str(item.get("canonical_tag", "")).strip()]
    character_entries = lookup_character_reference_entries(canonical_tags)
    if character_entries:
        parts.append("[Character Dictionary References]")
        for item in character_entries[:6]:
            line = (
                f"- {item.get('canonical_tag', '')}"
                f" | zh={compact_text(item.get('display_name_zh', ''))}"
                f" | copyright={item.get('copyright_tag', '')}"
                f" | trigger={compact_text(item.get('trigger_tag', ''), 120)}"
                f" | core_tags={compact_text(item.get('core_tags', ''), 140)}"
                f" | count={item.get('count', 0)}"
            )
            parts.append(line)

    should_use_tag_stats = input_requests_resource_role(inputs, "danbooru_tag_stats") or TAG_COUNT_TAGS_JSONL_PATH.exists()
    if should_use_tag_stats:
        raw_tag_candidates = split_tag_like_text(inputs.get("raw_tags", "") or inputs.get("raw_text", ""))
        tag_stats = lookup_tag_statistics_for_tags(raw_tag_candidates[:48])
        if tag_stats:
            parts.append("[Tag Frequency References]")
            for item in tag_stats[:12]:
                parts.append(f"- {item.get('tag', '')}: count={item.get('count', 0)}")

    if input_requests_resource_role(inputs, "danbooru_cooccurrence"):
        raw_tag_candidates = split_tag_like_text(inputs.get("raw_tags", "") or inputs.get("raw_text", ""))
        cooccurrence_hits = lookup_tag_cooccurrence_for_tags(raw_tag_candidates[:32])
        if cooccurrence_hits:
            parts.append("[Danbooru Cooccurrence Suggestions]")
            parts.append("Use these only when they match the user's intent; do not blindly add every related tag.")
            for item in cooccurrence_hits[:24]:
                parts.append(
                    f"- seed={item.get('seed', '')} -> related={item.get('related_tag', '')}, count={item.get('count', 0)}"
                )

    return "\n".join(parts).strip()


def resolve_task_request(task_type, inputs):
    prepared_inputs = dict(inputs or {})
    context_bundle = parse_context_bundle_json(prepared_inputs.get("context_bundle_json", ""))
    task_config = context_bundle.get("task_config", {})
    if not isinstance(task_config, dict):
        task_config = {}

    resolved_task_type = (
        str(task_config.get("task_type", "")).strip()
        or str(context_bundle.get("task_type", "")).strip()
        or str(task_type or "").strip()
    )

    fixed_inputs = {}
    for candidate in (context_bundle.get("fixed_inputs", {}), task_config.get("fixed_inputs", {})):
        if isinstance(candidate, dict):
            fixed_inputs.update(candidate)

    for key, value in fixed_inputs.items():
        existing = prepared_inputs.get(key, None)
        if existing is None or str(existing).strip() == "":
            prepared_inputs[key] = value

    for key in ("target_profile", "direction", "style_hint", "purpose", "image_path"):
        if str(prepared_inputs.get(key, "")).strip():
            continue
        config_value = str(task_config.get(key, "")).strip() or str(context_bundle.get(key, "")).strip()
        if config_value:
            prepared_inputs[key] = config_value

    return resolved_task_type, prepared_inputs


def apply_runtime_context(system_prompt, user_prompt, inputs):
    system_prompt = str(system_prompt or "")
    user_prompt = str(user_prompt or "")

    context_bundle = parse_context_bundle_json(inputs.get("context_bundle_json", ""))

    override = str(context_bundle.get("system_prompt_override", "") or inputs.get("system_prompt_override", "")).strip()
    if override:
        system_prompt = override + "\n\n" + system_prompt

    context_sections = []
    for key, title in (
        ("character_card_text", "Character Card"),
        ("world_book_text", "World Book"),
        ("regex_rules_text", "Regex Rules"),
        ("extra_notes_text", "Extra Notes"),
    ):
        value = str(context_bundle.get(key, "") or inputs.get(key, "")).strip()
        if value:
            context_sections.append(f"[{title}]\n{value}")

    bundle_raw_text = str(context_bundle.get("bundle_raw_text", "")).strip()
    if bundle_raw_text:
        context_sections.append(f"[Context Bundle Raw]\n{bundle_raw_text}")

    image_path = str(context_bundle.get("image_path", "") or inputs.get("image_path", "")).strip()
    if image_path:
        context_sections.append(f"[Image Path]\n{image_path}")

    resource_modules = context_bundle.get("resource_modules", [])
    if isinstance(resource_modules, list) and resource_modules:
        lines = []
        for item in resource_modules[:16]:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- role={role} | key={key} | path={path} | desc={desc}".format(
                    role=str(item.get("resource_role", "")).strip(),
                    key=str(item.get("resource_key", "")).strip(),
                    path=str(item.get("resource_path", "")).strip(),
                    desc=compact_text(str(item.get("description", "")).strip(), 100),
                )
            )
        if lines:
            context_sections.append("[Resource Modules]\n" + "\n".join(lines))

    local_dictionary_reference = str(inputs.get("local_dictionary_reference_text", "")).strip()
    if local_dictionary_reference:
        context_sections.append(f"[Local Dictionary References]\n{local_dictionary_reference}")

    if context_sections:
        user_prompt = user_prompt.rstrip() + "\n\n[Additional Runtime Context]\n" + "\n\n".join(context_sections)
    return system_prompt, user_prompt


def strip_markdown_fences(text):
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return stripped


def looks_like_jsonish_output(text):
    stripped = str(text or "").strip()
    if not stripped:
        return False
    if stripped.startswith("{") or stripped.startswith("```json") or stripped.startswith("```"):
        return True
    markers = (
        '"normalized_tags_en"',
        '"expanded_tags_en"',
        '"caption_long_en"',
        '"natural_language_en"',
        '"quality_tags_en"',
    )
    return any(marker in stripped for marker in markers)


def extract_jsonish_string_array(text, field_name, limit=64):
    pattern = re.compile(r'"' + re.escape(field_name) + r'"\s*:\s*\[(.*?)\]', re.DOTALL)
    match = pattern.search(str(text or ""))
    if not match:
        return []
    values = re.findall(r'"([^"]+)"', match.group(1))
    return dedupe_tags([value.strip() for value in values if value.strip()])[:limit]


def extract_jsonish_string_field(text, field_name, limit=320):
    pattern = re.compile(r'"' + re.escape(field_name) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)
    match = pattern.search(str(text or ""))
    if not match:
        return ""
    value = match.group(1).replace('\\"', '"').replace("\\n", " ")
    return compact_text(value, limit)


def extract_first_json_object(text):
    stripped = strip_markdown_fences(text)
    try:
        return json.loads(stripped)
    except Exception:
        pass

    start = stripped.find("{")
    if start < 0:
        raise ValueError("No JSON object start found in model output.")

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = stripped[start : index + 1]
                return json.loads(candidate)

    raise ValueError("No complete JSON object found in model output.")


def split_tag_like_text(text):
    normalized = (
        text.replace("\r", "\n")
        .replace("，", ",")
        .replace("、", ",")
        .replace("；", ",")
        .replace(";", ",")
        .replace("|", ",")
    )
    pieces = []
    for chunk in normalized.splitlines():
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith(("-", "*", "•")):
            chunk = chunk[1:].strip()
        for part in chunk.split(","):
            cleaned = part.strip().strip(".")
            if cleaned:
                pieces.append(cleaned)
    deduped = []
    seen = set()
    for item in pieces:
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(item)
    return deduped


def normalize_text_for_search(text):
    return str(text or "").strip().lower().replace("_", " ").replace("-", " ")


def tokenize_search_text(text):
    normalized = normalize_text_for_search(text)
    raw_tokens = []
    current = []
    for char in normalized:
        if "\u4e00" <= char <= "\u9fff":
            current.append(char)
        else:
            if current:
                raw_tokens.append("".join(current))
                current = []
    if current:
        raw_tokens.append("".join(current))
    for token in normalized.replace("/", " ").replace(",", " ").split():
        cleaned = token.strip()
        if cleaned:
            raw_tokens.append(cleaned)
    return dedupe_tags(raw_tokens)


def infer_outfit_context(inputs):
    season = str(inputs.get("season", "")).strip().lower()
    scene = str(inputs.get("outfit_scene", "")).strip()
    style_direction = str(inputs.get("style_direction", "")).strip()
    personality_traits = str(inputs.get("personality_traits", "")).strip()
    design_constraints = str(inputs.get("design_constraints", "")).strip()
    character_description = str(inputs.get("character_description", "")).strip()

    combined_raw = "\n".join(
        [character_description, personality_traits, scene, season, style_direction, design_constraints]
    )
    combined_norm = normalize_text_for_search(combined_raw)
    season_norm = normalize_text_for_search(season)

    if any(token in season_norm for token in ("summer", "夏", "盛夏", "炎热")):
        season_bucket = "summer"
    elif any(token in season_norm for token in ("winter", "冬", "寒", "冷")):
        season_bucket = "winter"
    elif any(token in season_norm for token in ("autumn", "fall", "秋")):
        season_bucket = "autumn"
    else:
        season_bucket = "spring"

    is_stage = any(token in combined_raw for token in ("舞台", "演出", "打歌", "live", "concert", "performance"))
    is_daily = not is_stage and (
        any(token in combined_raw for token in ("日常", "私服", "校园", "学生", "通学", "街", "咖啡馆", "约会"))
        or True
    )

    mood_tags = set()
    flags = set()
    mood_rules = {
        "gentle": ("温柔", "柔和", "安静", "文静", "软", "gentle", "soft"),
        "cool": ("冷淡", "冷静", "利落", "酷", "cool", "sharp"),
        "cute": ("可爱", "甜", "元气", "俏皮", "cute", "sweet"),
        "mature": ("成熟", "知性", "稳重", "大人", "mature", "elegant"),
        "sporty": ("运动", "活泼", "轻快", "sporty", "active", "casual"),
    }
    for mood, markers in mood_rules.items():
        if any(marker in combined_raw or marker in combined_norm for marker in markers):
            mood_tags.add(mood)

    if any(marker in combined_raw or marker in combined_norm for marker in ("jk", "校服", "制服", "学院", "学院风", "preppy")):
        flags.add("student_uniform_bias")

    if "cool" in mood_tags or "sporty" in mood_tags or any(marker in combined_raw for marker in ("利落", "中性", "干练", "裤装", "不穿裙")):
        bottom_mode = "pants"
    elif "gentle" in mood_tags or "mature" in mood_tags or any(marker in combined_raw for marker in ("长裙", "温柔长裙", "半身裙")):
        bottom_mode = "long_skirt"
    elif "cute" in mood_tags or "student_uniform_bias" in flags or any(marker in combined_raw for marker in ("可爱裙", "百褶裙", "短裙")):
        bottom_mode = "skirt"
    else:
        bottom_mode = "balanced"

    return {
        "season_bucket": season_bucket,
        "is_daily": is_daily,
        "is_stage": is_stage,
        "mood_tags": mood_tags,
        "prefer_light_structure": season_bucket in {"spring", "summer"},
        "bottom_mode": bottom_mode,
        "flags": flags,
    }


def should_block_clothing_record(record, context):
    tag = str(record.get("tag", "")).strip().lower()
    if not tag:
        return True
    if context.get("is_daily"):
        if tag in GENERIC_DAILY_BLOCKED_TAGS:
            return True
        if any(fragment in tag for fragment in GENERIC_DAILY_BLOCKED_SUBSTRINGS):
            return True
    return False


def classify_bottom_family(record):
    tag = str(record.get("tag", "")).strip().lower()
    if tag in {"jeans", "trousers", "capris", "shorts", "denim_shorts", "bike_shorts"}:
        return "pants"
    if "skirt" in tag:
        return "skirt"
    return "other"


def score_clothing_record(item, context):
    record = item["record"]
    score = item["score"]
    tag = str(record.get("tag", "")).strip().lower()
    slot = classify_clothing_slot(record)
    definition = normalize_text_for_search(record.get("definition", ""))

    if tag in SAFE_DAILY_TAG_BOOSTS.get(slot, set()):
        score += 3
    if tag in {"top", "pants"}:
        score -= 3

    if context.get("prefer_light_structure"):
        if slot == "outer":
            score -= 2
        if slot in {"top", "onepiece"}:
            score += 2
        if tag in {"hooded_jacket", "coat", "trench_coat"}:
            score -= 3
    else:
        if slot == "outer":
            score += 1
        if tag in {"cardigan", "sweater", "blazer", "jacket"}:
            score += 2

    mood_tags = context.get("mood_tags", set())
    if "gentle" in mood_tags and tag in {"blouse", "dress", "jumper_dress", "long_skirt", "cardigan"}:
        score += 3
    if "cool" in mood_tags and tag in {"shirt", "jacket", "hooded_jacket", "jeans", "trousers", "ankle_boots"}:
        score += 3
    if "cute" in mood_tags and tag in {"pleated_skirt", "jumper_dress", "pinafore_dress", "sweater_vest", "hair_ribbon"}:
        score += 3
    if "mature" in mood_tags and tag in {"blouse", "blazer", "long_skirt", "ankle_boots"}:
        score += 2
    if "sporty" in mood_tags and tag in {"hoodie", "sneakers", "jacket", "jeans"}:
        score += 3

    if slot == "bottom":
        bottom_mode = context.get("bottom_mode", "balanced")
        family = classify_bottom_family(record)
        if bottom_mode == "pants":
            if family == "pants":
                score += 4
            elif family == "skirt":
                score -= 2
        elif bottom_mode == "skirt":
            if family == "skirt":
                score += 3
            elif family == "pants":
                score -= 1
        elif bottom_mode == "long_skirt":
            if tag == "long_skirt":
                score += 5
            elif family == "pants":
                score -= 1

        if tag == "pleated_skirt":
            if bottom_mode == "pants":
                score -= 4
            elif "cute" not in mood_tags and "student_uniform_bias" not in context.get("flags", set()):
                score -= 2

    if "日常" in definition or "常见" in definition:
        score += 1
    if any(word in definition for word in ("军礼服", "礼仪", "戏剧", "历史", "传统风格", "职业辨识")):
        score -= 3

    item["score"] = score
    item["slot"] = slot
    return item


def build_fallback_clothing_candidates(context):
    results = []
    season_bucket = context.get("season_bucket")
    mood_tags = context.get("mood_tags", set())

    tag_plan = {slot: list(tags) for slot, tags in DAILY_FALLBACK_TAGS.items()}
    if season_bucket in {"spring", "summer"}:
        tag_plan["outer"] = ["cardigan", "blazer", "jacket"]
        tag_plan["onepiece"] = ["dress", "jumper_dress", "pinafore_dress"]
    if season_bucket in {"autumn", "winter"}:
        tag_plan["top"] = ["sweater", "pullover", "shirt", "blouse", "hoodie", "sweater_vest"]
        tag_plan["outer"] = ["cardigan", "jacket", "blazer", "hooded_jacket"]

    if "gentle" in mood_tags:
        tag_plan["top"] = ["blouse", "shirt", "sweater_vest", "pullover"] + tag_plan["top"]
        tag_plan["bottom"] = ["long_skirt", "frilled_skirt", "pleated_skirt"] + tag_plan["bottom"]
        tag_plan["detail"] = ["neck_ribbon", "pendant", "ribbon_trim", "hair_ribbon"] + tag_plan["detail"]
    if "cool" in mood_tags:
        tag_plan["top"] = ["shirt", "hoodie", "pullover"] + tag_plan["top"]
        tag_plan["bottom"] = ["trousers", "jeans", "capris"] + tag_plan["bottom"]
        tag_plan["footwear"] = ["ankle_boots", "sneakers", "loafers"] + tag_plan["footwear"]
        tag_plan["detail"] = ["open_collar", "sleeves_pushed_up", "belt", "choker"] + tag_plan["detail"]
    if "cute" in mood_tags:
        tag_plan["onepiece"] = ["jumper_dress", "pinafore_dress", "dress"] + tag_plan["onepiece"]
        tag_plan["bottom"] = ["pleated_skirt", "frilled_skirt", "miniskirt"] + tag_plan["bottom"]
        tag_plan["accessory"] = ["hair_ribbon", "hair_bow", "hair_pin"] + tag_plan["accessory"]
        tag_plan["detail"] = ["bowtie", "neck_ribbon", "ribbon_trim", "shoe_ribbon"] + tag_plan["detail"]
    if "sporty" in mood_tags:
        tag_plan["top"] = ["hoodie", "shirt", "pullover"] + tag_plan["top"]
        tag_plan["bottom"] = ["jeans", "shorts", "capris"] + tag_plan["bottom"]
        tag_plan["footwear"] = ["sneakers", "loafers"] + tag_plan["footwear"]
        tag_plan["detail"] = ["open_jacket", "sleeves_pushed_up", "belt"] + tag_plan["detail"]

    base_score_by_slot = {
        "style": 8,
        "top": 10,
        "outer": 9,
        "onepiece": 10,
        "bottom": 10,
        "legwear": 8,
        "footwear": 9,
        "accessory": 7,
    }
    for slot, tags in tag_plan.items():
        seen = set()
        for index, tag in enumerate(tags):
            lowered = tag.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            record = CLOTHING_RECORDS_BY_TAG.get(lowered)
            if not record or should_block_clothing_record(record, context):
                continue
            results.append(
                score_clothing_record(
                    {
                        "score": max(1, base_score_by_slot.get(slot, 6) - index),
                        "record": record,
                    },
                    context,
                )
            )
    return results


def merge_candidate_items(primary_items, fallback_items):
    merged = {}
    for item in list(primary_items) + list(fallback_items):
        tag = str(item["record"].get("tag", "")).strip().lower()
        if not tag:
            continue
        existing = merged.get(tag)
        if existing is None or item["score"] > existing["score"]:
            merged[tag] = item
    return sorted(
        merged.values(),
        key=lambda item: (
            -item["score"],
            -len(str(item["record"].get("tag", ""))),
            str(item["record"].get("tag", "")),
        ),
    )


def search_clothing_records(query_text, limit=18, context=None):
    if not CLOTHING_RECORDS:
        return []

    query_raw = str(query_text or "").strip()
    if not query_raw:
        return []

    query_normalized = normalize_text_for_search(query_raw)
    query_tokens = tokenize_search_text(query_raw)
    results = []
    for record in CLOTHING_RECORDS:
        if record.get("node_type") != "tag":
            continue
        score = 0
        name_zh = str(record.get("name_zh", "")).strip()
        name_en = normalize_text_for_search(record.get("name_en", ""))
        tag = normalize_text_for_search(record.get("tag", ""))
        definition = normalize_text_for_search(record.get("definition", ""))
        group_zh = str(record.get("group_zh", "")).strip()
        top_category = str(record.get("top_category", "")).strip()
        keywords = [normalize_text_for_search(item) for item in record.get("keywords", [])]

        if name_zh and name_zh in query_raw:
            score += 14
        if tag and tag in query_normalized:
            score += 12
        if name_en and name_en in query_normalized:
            score += 10
        for keyword in keywords:
            if not keyword:
                continue
            if contains_cjk(keyword):
                if len(keyword) >= 2 and keyword in query_raw:
                    score += 5
            else:
                if len(keyword) >= 3 and keyword in query_normalized:
                    score += 4
        for token in query_tokens:
            token_norm = normalize_text_for_search(token)
            if not token_norm:
                continue
            if contains_cjk(token):
                if len(token) >= 2 and (
                    token in name_zh
                    or token in group_zh
                    or token in top_category
                    or token in str(record.get("definition", ""))
                ):
                    score += 2
            else:
                if len(token_norm) >= 3 and (
                    token_norm == tag
                    or token_norm == name_en
                    or token_norm in definition
                ):
                    score += 2

        if score <= 0:
            continue
        item = {"score": score, "record": record}
        if context and should_block_clothing_record(record, context):
            continue
        results.append(score_clothing_record(item, context or {}))

    results.sort(
        key=lambda item: (
            -item["score"],
            -len(str(item["record"].get("tag", ""))),
            str(item["record"].get("tag", "")),
        )
    )

    selected = []
    seen_tags = set()
    for item in results:
        tag = str(item["record"].get("tag", "")).strip()
        if not tag or tag in seen_tags:
            continue
        seen_tags.add(tag)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def format_clothing_candidates_for_prompt(candidates):
    lines = []
    for item in candidates:
        record = item["record"]
        lines.append(
            f'- {record.get("name_zh", "")} | {record.get("tag", "")} | '
            f'{record.get("group_zh", "")} | {record.get("definition", "")}'
        )
    return "\n".join(lines)


def classify_clothing_slot(record):
    path_zh = " ".join(record.get("path_zh", []))
    group_zh = str(record.get("group_zh", ""))
    name_zh = str(record.get("name_zh", ""))
    tag = str(record.get("tag", ""))
    text = " ".join([path_zh, group_zh, name_zh, tag]).lower()

    if any(keyword in text for keyword in ("neckwear", "颈部服饰", "扣件与装饰", "decorations", "服装状态与杂项", "clothing states / misc")):
        return "detail"
    if tag in {"open_collar", "open_jacket", "loose_necktie", "undone_necktie", "sleeves_pushed_up", "unbuttoned", "bowtie", "neck_ribbon", "necktie", "choker", "necklace", "pendant", "belt", "waist_sash", "ribbon_trim", "belt_charm", "shoe_ribbon"}:
        return "detail"
    if tag in {"hoodie", "shirt", "dress_shirt", "open_shirt", "blouse", "sweater", "pullover", "top", "vest", "sweater_vest", "waistcoat", "turtleneck", "tank_top"}:
        return "top"
    if tag in {"cardigan", "jacket", "hooded_jacket", "blazer", "coat"}:
        return "outer"
    if tag in {"dress", "sweater_dress", "pinafore_dress", "jumper_dress", "romper"}:
        return "onepiece"
    if tag in {"jeans", "trousers", "pants", "pleated_skirt", "long_skirt", "frilled_skirt", "pencil_skirt", "skirt"}:
        return "bottom"
    if tag in {"loafers", "sneakers", "ankle_boots", "mary_janes", "boots", "uwabaki"}:
        return "footwear"
    if "时尚风格" in path_zh or "fashion style" in text:
        return "style"
    if "鞋" in path_zh or "footwear" in text or "boots" in text or "loafers" in text or "sneakers" in text:
        return "footwear"
    if "袜" in path_zh or "legwear" in text or "tights" in text or "socks" in text or "thighhighs" in text:
        return "legwear"
    if "配饰" in path_zh or "饰品" in path_zh or "accessor" in text or "necklace" in text or "choker" in text or "bracelet" in text:
        return "accessory"
    if "帽子" in path_zh or "头部装束" in path_zh or "headgear" in text or "hair_ornament" in tag:
        return "accessory"
    if "连衣" in path_zh or "dress" == tag or "dress" in text or "pinafore" in tag or "suspender_skirt" in tag:
        return "onepiece"
    if "下装" in path_zh or "skirt" in tag or "pants" in tag or "jeans" in tag or "shorts" in tag:
        return "bottom"
    if "外套" in path_zh or "outerwear" in text or "jacket" in tag or "hoodie" in tag or "coat" in tag or "cardigan" in tag:
        return "outer"
    if "上装" in path_zh or "tops" in text or "shirt" in tag or "blouse" in tag or "t-shirt" in tag or "vest" in tag or "turtleneck" in tag or "camisole" in tag:
        return "top"
    return "other"


def select_balanced_clothing_candidates(candidates, context=None):
    slots = {
        "style": [],
        "top": [],
        "outer": [],
        "onepiece": [],
        "bottom": [],
        "legwear": [],
        "footwear": [],
        "accessory": [],
        "detail": [],
        "other": [],
    }
    for item in candidates:
        slots[classify_clothing_slot(item["record"])].append(item)

    bottom_mode = (context or {}).get("bottom_mode", "balanced")
    bottom_pants = []
    bottom_skirts = []
    bottom_other = []
    for item in slots["bottom"]:
        family = classify_bottom_family(item["record"])
        if family == "pants":
            bottom_pants.append(item)
        elif family == "skirt":
            bottom_skirts.append(item)
        else:
            bottom_other.append(item)

    if (context or {}).get("prefer_light_structure"):
        quota_plan = [
            ("style", 4),
            ("top", 7),
            ("outer", 2),
            ("onepiece", 5),
            ("bottom", 6),
            ("legwear", 3),
            ("footwear", 4),
            ("accessory", 3),
            ("detail", 4),
            ("other", 2),
        ]
    else:
        quota_plan = [
            ("style", 4),
            ("top", 6),
            ("outer", 4),
            ("onepiece", 4),
            ("bottom", 6),
            ("legwear", 4),
            ("footwear", 4),
            ("accessory", 4),
            ("detail", 4),
            ("other", 2),
        ]

    selected = []
    for slot_name, take_count in quota_plan:
        if slot_name != "bottom":
            selected.extend(slots[slot_name][:take_count])
            continue

        if bottom_mode == "pants":
            picks = bottom_pants[:4] + bottom_skirts[:2] + bottom_other[:1]
        elif bottom_mode == "skirt":
            picks = bottom_skirts[:4] + bottom_pants[:2] + bottom_other[:1]
        elif bottom_mode == "long_skirt":
            long_skirt_first = [item for item in bottom_skirts if str(item["record"].get("tag", "")).strip().lower() == "long_skirt"]
            other_skirts = [item for item in bottom_skirts if str(item["record"].get("tag", "")).strip().lower() != "long_skirt"]
            picks = long_skirt_first[:2] + other_skirts[:2] + bottom_pants[:2] + bottom_other[:1]
        else:
            picks = bottom_pants[:3] + bottom_skirts[:3] + bottom_other[:1]

        deduped_bottom = []
        seen_bottom = set()
        for item in picks:
            tag = str(item["record"].get("tag", "")).strip().lower()
            if not tag or tag in seen_bottom:
                continue
            seen_bottom.add(tag)
            deduped_bottom.append(item)
        selected.extend(deduped_bottom[:take_count])
    return selected, slots


def format_structured_clothing_candidates(slot_map):
    slot_titles = {
        "style": "风格候选",
        "top": "上衣候选",
        "outer": "外层候选",
        "onepiece": "连衣式候选",
        "bottom": "下装候选",
        "legwear": "袜装候选",
        "footwear": "鞋类候选",
        "accessory": "配饰候选",
        "detail": "点缀与状态候选",
    }
    lines = []
    for slot in ["style", "top", "outer", "onepiece", "bottom", "legwear", "footwear", "accessory", "detail"]:
        items = slot_map.get(slot, [])
        if not items:
            continue
        lines.append(f"[{slot_titles[slot]}]")
        for item in items:
            record = item["record"]
            lines.append(
                f'- {record.get("name_zh", "")} | {record.get("tag", "")} | '
                f'{record.get("group_zh", "")} | {record.get("definition", "")}'
            )
    return "\n".join(lines)


def dedupe_tags(tags):
    result = []
    seen = set()
    for item in tags:
        cleaned = str(item).strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(cleaned)
    return result


def is_quality_control_tag(tag):
    lowered = normalize_text_for_search(tag)
    if not lowered:
        return False
    if lowered in QUALITY_CONTROL_EXACT_TAGS:
        return True
    if lowered.startswith("score "):
        return True
    return False


def strip_quality_control_tags(tags):
    return [tag for tag in dedupe_tags(tags or []) if not is_quality_control_tag(tag)]


def is_globally_blocked_tag(tag):
    return normalize_alias_key(tag) in GLOBAL_BLOCKED_TAG_KEYS


def filter_globally_blocked_tags(tags):
    return [tag for tag in dedupe_tags(tags or []) if not is_globally_blocked_tag(tag)]


def build_alias_reference_text(alias_hits):
    if not alias_hits:
        return ""
    lines = ["角色规范 tag 参考："]
    for hit in alias_hits:
        lines.append(f'- "{hit["matched_alias"]}" -> "{hit["canonical_tag"]}"')
    lines.append("如果涉及这些角色，必须直接使用右侧规范 Danbooru tag，不要自行翻译角色名。")
    return "\n".join(lines)


def detect_character_aliases_in_text(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return []

    normalized_text = normalize_alias_key(raw_text)
    padded_normalized_text = f" {normalized_text} "
    candidates = []
    for alias_key, data in CHARACTER_ALIASES.items():
        if not alias_key:
            continue
        matched = False
        match_length = len(data["matched_alias"])
        start_index = -1
        if contains_cjk(data["matched_alias"]):
            if len(data["matched_alias"]) < 3:
                continue
            start_index = raw_text.find(data["matched_alias"])
            matched = start_index >= 0
        else:
            if should_skip_character_alias(
                data["matched_alias"],
                data["canonical_tag"],
                data.get("_metadata", {}),
                mode="free_text",
            ):
                continue
            if len(alias_key) < 3:
                continue
            probe = f" {alias_key} "
            start_index = padded_normalized_text.find(probe)
            matched = start_index >= 0 or normalized_text == alias_key
        if not matched:
            continue
        candidates.append(
            {
                "matched_alias": data["matched_alias"],
                "canonical_tag": data["canonical_tag"],
                "blocked_tags": list(data.get("blocked_tags", [])),
                "_length": match_length,
                "_start": start_index if start_index >= 0 else 0,
            }
        )

    candidates.sort(key=lambda item: (-item["_length"], item["_start"]))
    hits = []
    seen = set()
    occupied = []
    for candidate in candidates:
        canonical = candidate["canonical_tag"]
        if canonical in seen:
            continue
        start = candidate["_start"]
        end = start + candidate["_length"]
        overlap = any(not (end <= occ_start or start >= occ_end) for occ_start, occ_end in occupied)
        if overlap:
            continue
        occupied.append((start, end))
        seen.add(canonical)
        hits.append(
            {
                "matched_alias": candidate["matched_alias"],
                "canonical_tag": canonical,
                "blocked_tags": candidate["blocked_tags"],
            }
        )
    return hits


def resolve_character_aliases_in_tag_text(raw_tags):
    tokens = split_tag_like_text(raw_tags)
    if not tokens:
        return raw_tags, []

    resolved = []
    hits = []
    seen_canonical = set()
    for token in tokens:
        alias = CHARACTER_ALIASES.get(normalize_alias_key(token))
        if alias:
            if should_skip_character_alias(
                alias["matched_alias"],
                alias["canonical_tag"],
                alias.get("_metadata", {}),
                mode="tag_text",
            ):
                resolved.append(token)
                continue
            canonical = alias["canonical_tag"]
            if is_globally_blocked_tag(canonical):
                continue
            resolved.append(canonical)
            if canonical not in seen_canonical:
                seen_canonical.add(canonical)
                hits.append(
                    {
                        "matched_alias": token,
                        "canonical_tag": canonical,
                        "blocked_tags": list(alias.get("blocked_tags", [])),
                    }
                )
        else:
            resolved.append(token)
    return ", ".join(dedupe_tags(resolved)), hits


def detect_character_aliases_in_tag_list(raw_tags):
    tokens = split_tag_like_text(raw_tags)
    if not tokens:
        return []

    hits = []
    seen_canonical = set()
    for token in tokens:
        alias = CHARACTER_ALIASES.get(normalize_alias_key(token))
        if not alias:
            continue
        if should_skip_character_alias(
            alias["matched_alias"],
            alias["canonical_tag"],
            alias.get("_metadata", {}),
            mode="tag_text",
        ):
            continue
        canonical = alias["canonical_tag"]
        if canonical in seen_canonical or is_globally_blocked_tag(canonical):
            continue
        seen_canonical.add(canonical)
        hits.append(
            {
                "matched_alias": token,
                "canonical_tag": canonical,
                "blocked_tags": list(alias.get("blocked_tags", [])),
            }
        )
    return hits


def preprocess_task_inputs(task_type, inputs):
    prepared = dict(inputs or {})
    alias_hits = []
    original_raw_tags = str(prepared.get("raw_tags", "")).strip()
    if original_raw_tags and not prepared.get("wd14_raw_tags_en"):
        prepared["wd14_raw_tags_en"] = split_tag_like_text(original_raw_tags)

    if task_type in ("expand_anime_tags", "normalize_anime_tags"):
        rewritten_tags, tag_hits = resolve_character_aliases_in_tag_text(prepared.get("raw_tags", ""))
        prepared["raw_tags"] = rewritten_tags
        alias_hits.extend(tag_hits)

    if task_type in ("refine_wd14_tags", "generate_natural_caption"):
        alias_hits.extend(detect_character_aliases_in_tag_list(prepared.get("raw_tags", "")))

    if task_type in ("translate_anime_tags", "generate_natural_caption", "refine_wd14_tags"):
        alias_hits.extend(detect_character_aliases_in_text(prepared.get("raw_text", "")))

    if task_type == "generate_outfit_tags":
        alias_hits.extend(detect_character_aliases_in_text(prepared.get("character_description", "")))
        alias_hits.extend(detect_character_aliases_in_text(prepared.get("reference_notes", "")))

    deduped_hits = []
    seen = set()
    for hit in alias_hits:
        canonical = hit["canonical_tag"]
        if canonical in seen:
            continue
        seen.add(canonical)
        deduped_hits.append(hit)
    prepared["resolved_character_tags"] = deduped_hits
    prepared["resolved_character_tag_strings"] = [hit["canonical_tag"] for hit in deduped_hits]
    prepared["local_dictionary_reference_text"] = build_local_dictionary_reference_text(prepared)
    return prepared


def prepend_canonical_tags(tags, canonical_tags):
    return dedupe_tags(list(canonical_tags) + list(tags or []))


def filter_blocked_tags(tags, alias_hits):
    blocked = {normalize_alias_key(tag) for hit in alias_hits for tag in hit.get("blocked_tags", []) if str(tag).strip()}
    if not blocked:
        return dedupe_tags(tags or [])
    filtered = []
    for tag in tags or []:
        if normalize_alias_key(tag) in blocked:
            continue
        filtered.append(tag)
    return dedupe_tags(filtered)


def apply_character_aliases_to_result(task_type, json_result, inputs):
    alias_hits = inputs.get("resolved_character_tags", [])
    if not alias_hits:
        return json_result

    canonical_tags = [hit["canonical_tag"] for hit in alias_hits]
    enriched = dict(json_result)
    enriched["resolved_character_tags"] = alias_hits

    if task_type == "expand_anime_tags":
        normalized = filter_globally_blocked_tags(filter_blocked_tags(enriched.get("normalized_tags_en", []), alias_hits))
        expanded = filter_globally_blocked_tags(filter_blocked_tags(enriched.get("expanded_tags_en", []), alias_hits))
        enriched["normalized_tags_en"] = prepend_canonical_tags(normalized, canonical_tags)
        enriched["expanded_tags_en"] = prepend_canonical_tags(expanded, canonical_tags)
        return enriched

    if task_type == "translate_anime_tags":
        tag_list = filter_globally_blocked_tags(filter_blocked_tags(enriched.get("tag_list", []), alias_hits))
        tag_list = prepend_canonical_tags(tag_list, canonical_tags)
        enriched["tag_list"] = tag_list
        if inputs.get("direction") == "zh_to_en_tags":
            enriched["translated_text"] = ", ".join(tag_list)
        return enriched

    if task_type == "normalize_anime_tags":
        normalized = filter_globally_blocked_tags(filter_blocked_tags(enriched.get("normalized_tags_en", []), alias_hits))
        recommended = filter_globally_blocked_tags(filter_blocked_tags(enriched.get("recommended_prompt_order_en", []), alias_hits))
        enriched["normalized_tags_en"] = prepend_canonical_tags(normalized, canonical_tags)
        enriched["recommended_prompt_order_en"] = prepend_canonical_tags(
            recommended, canonical_tags
        )
        return enriched

    if task_type == "generate_outfit_tags":
        normalized = filter_globally_blocked_tags(filter_blocked_tags(enriched.get("normalized_tags_en", []), alias_hits))
        expanded = filter_globally_blocked_tags(filter_blocked_tags(enriched.get("expanded_tags_en", []), alias_hits))
        style_tags = filter_globally_blocked_tags(filter_blocked_tags(enriched.get("style_tags_en", []), alias_hits))
        detail_tags = filter_globally_blocked_tags(filter_blocked_tags(enriched.get("detail_tags_en", []), alias_hits))
        enriched["normalized_tags_en"] = prepend_canonical_tags(normalized, canonical_tags)
        enriched["expanded_tags_en"] = prepend_canonical_tags(style_tags + detail_tags + expanded, canonical_tags)
        return enriched

    if task_type in ("extract_tags_from_image", "vision_tagging", "image_captioning", "refine_wd14_tags", "generate_natural_caption"):
        for field_name in (
            "normalized_tags_en",
            "expanded_tags_en",
            "tag_list",
            "character_tags_en",
            "appearance_tags_en",
            "outfit_tags_en",
            "expression_tags_en",
            "pose_tags_en",
            "camera_tags_en",
            "style_tags_en",
        ):
            field_value = enriched.get(field_name, [])
            if not isinstance(field_value, list):
                continue
            filtered = filter_globally_blocked_tags(filter_blocked_tags(field_value, alias_hits))
            if field_name in {"normalized_tags_en", "expanded_tags_en", "tag_list", "character_tags_en"}:
                filtered = prepend_canonical_tags(filtered, canonical_tags)
            enriched[field_name] = filtered
        return enriched

    return enriched


def normalize_tag_for_anima(tag):
    lowered = tag.strip().lower()
    if not lowered:
        return ""
    if lowered.startswith("score "):
        return lowered.replace(" ", "_")
    if lowered.startswith("score_"):
        return lowered
    return escape_sdxl_tag_parentheses(lowered.replace("_", " "))


def normalize_tag_for_noobai(tag):
    lowered = tag.strip().lower()
    if not lowered:
        return ""
    return escape_sdxl_tag_parentheses(lowered.replace("_", " "))


def normalize_tag_for_illustrious(tag):
    lowered = tag.strip().lower()
    if not lowered:
        return ""
    return escape_sdxl_tag_parentheses(lowered)


def escape_sdxl_tag_parentheses(tag):
    return str(tag).replace("(", "\\(").replace(")", "\\)")


def detect_r18_intent(tags):
    lowered_tags = [str(tag).strip().lower() for tag in tags if str(tag).strip()]
    joined = ", ".join(lowered_tags)
    markers = [
        "nsfw",
        "rating explicit",
        "rating_explicit",
        "explicit",
        "nude",
        "naked",
        "sex",
        "pussy",
        "nipples",
        "breasts out",
    ]
    return any(marker in joined for marker in markers)


def build_quality_tail_for_illustrious_or_noobai(include_r18):
    tail = [
        "masterpiece",
        "absurdres",
        "highres",
        "very awa",
        "best quality",
        "high resolution",
        "aesthetic",
        "excellent",
        "year 2025",
        "newest",
        "uncensored",
    ]
    if include_r18:
        tail.extend(["NSFW", "rating explicit"])
    return tail


EXPRESSION_TAG_MARKERS = (
    "smile",
    "grin",
    "frown",
    "angry",
    "sad",
    "crying",
    "tears",
    "blush",
    "embarrassed",
    "surprised",
    "shocked",
    "stern",
    "serious",
    "laugh",
    "laughing",
    "open mouth",
    "closed mouth",
    "pout",
    "wince",
    "smirk",
)

POSE_TAG_MARKERS = (
    "standing",
    "sitting",
    "kneeling",
    "running",
    "walking",
    "leaning",
    "arms",
    "hand on",
    "looking at viewer",
    "looking away",
    "turned",
    "head tilt",
    "raised hand",
    "crossed arms",
)

CAMERA_TAG_MARKERS = (
    "close-up",
    "upper body",
    "cowboy shot",
    "full body",
    "portrait",
    "profile view",
    "from above",
    "from below",
    "dutch angle",
    "wide shot",
    "looking at viewer",
)

STYLE_TAG_MARKERS = (
    "anime",
    "galgame",
    "digital art",
    "illustration",
    "lineart",
    "cel shading",
    "scenic",
    "cinematic",
    "soft lighting",
    "rim light",
    "monochrome",
    "painterly",
)

SAFETY_TAG_MARKERS = (
    "nsfw",
    "rating explicit",
    "rating_explicit",
    "explicit",
    "safe",
)

OUTFIT_TAG_MARKERS = (
    "shirt",
    "blouse",
    "sweater",
    "hoodie",
    "dress",
    "skirt",
    "jacket",
    "coat",
    "uniform",
    "pants",
    "trousers",
    "shorts",
    "socks",
    "boots",
    "shoes",
    "ribbon",
    "bow",
    "hat",
    "gloves",
)


def tag_matches_any_marker(tag, markers):
    normalized = normalize_lookup_tag(tag)
    return any(marker in normalized for marker in markers)


def infer_structured_tag_sections(json_result, inputs):
    working = dict(json_result or {})
    normalized_tags = dedupe_tags(filter_globally_blocked_tags(working.get("normalized_tags_en", [])))
    expanded_tags = dedupe_tags(filter_globally_blocked_tags(working.get("expanded_tags_en", [])))
    translated_tags = dedupe_tags(filter_globally_blocked_tags(working.get("tag_list", [])))
    input_seed_tags = split_tag_like_text(inputs.get("raw_tags", "") or inputs.get("raw_text", ""))
    all_tags = dedupe_tags(expanded_tags or normalized_tags or translated_tags or input_seed_tags)

    character_tags = dedupe_tags(
        working.get("character_tags_en", [])
        + inputs.get("resolved_character_tag_strings", [])
    )
    outfit_tags = dedupe_tags(working.get("outfit_tags_en", []))
    expression_tags = dedupe_tags(working.get("expression_tags_en", []))
    pose_tags = dedupe_tags(working.get("pose_tags_en", []))
    camera_tags = dedupe_tags(working.get("camera_tags_en", []))
    style_tags = dedupe_tags(working.get("style_tags_en", []))
    safety_tags = dedupe_tags(working.get("safety_tags_en", []))
    appearance_tags = dedupe_tags(working.get("appearance_tags_en", []))

    used_keys = {normalize_lookup_tag(tag) for tag in character_tags}
    for tag in all_tags:
        key = normalize_lookup_tag(tag)
        if not key or key in used_keys:
            continue
        if key in CLOTHING_RECORDS_BY_TAG or tag_matches_any_marker(tag, OUTFIT_TAG_MARKERS):
            outfit_tags.append(tag)
        elif tag_matches_any_marker(tag, EXPRESSION_TAG_MARKERS):
            expression_tags.append(tag)
        elif tag_matches_any_marker(tag, POSE_TAG_MARKERS):
            pose_tags.append(tag)
        elif tag_matches_any_marker(tag, CAMERA_TAG_MARKERS):
            camera_tags.append(tag)
        elif tag_matches_any_marker(tag, STYLE_TAG_MARKERS):
            style_tags.append(tag)
        elif tag_matches_any_marker(tag, SAFETY_TAG_MARKERS):
            safety_tags.append(tag)
        else:
            appearance_tags.append(tag)
        used_keys.add(key)

    quality_tags = dedupe_tags(working.get("quality_tags_en", []))
    negative_tags = dedupe_tags(filter_globally_blocked_tags(working.get("negative_tags_en", [])))

    subject_parts = []
    if character_tags:
        subject_parts.append(", ".join(character_tags[:3]))
    if appearance_tags:
        subject_parts.append(", ".join(appearance_tags[:5]))
    if outfit_tags:
        subject_parts.append("wearing " + ", ".join(outfit_tags[:4]))
    if expression_tags:
        subject_parts.append("with " + ", ".join(expression_tags[:3]))
    if camera_tags:
        subject_parts.append(", ".join(camera_tags[:2]))

    caption_short = str(working.get("caption_short_en", "")).strip()
    caption_long = str(working.get("caption_long_en", "")).strip()
    natural_language = str(working.get("natural_language_en", "")).strip()

    if not natural_language and subject_parts:
        natural_language = compact_text("anime illustration of " + ", ".join(subject_parts), 220)
    if not caption_short:
        caption_short = natural_language or compact_text(", ".join(all_tags[:16]), 180)
    if not caption_long:
        long_parts = []
        if caption_short:
            long_parts.append(caption_short.rstrip("."))
        if style_tags:
            long_parts.append("style: " + ", ".join(style_tags[:4]))
        if quality_tags:
            long_parts.append("quality focus: " + ", ".join(quality_tags[:4]))
        caption_long = ". ".join([part for part in long_parts if part]).strip()

    working["character_tags_en"] = dedupe_tags(character_tags)
    working["appearance_tags_en"] = dedupe_tags(appearance_tags)
    working["outfit_tags_en"] = dedupe_tags(outfit_tags)
    working["expression_tags_en"] = dedupe_tags(expression_tags)
    working["pose_tags_en"] = dedupe_tags(pose_tags)
    working["camera_tags_en"] = dedupe_tags(camera_tags)
    working["style_tags_en"] = dedupe_tags(style_tags)
    working["safety_tags_en"] = dedupe_tags(safety_tags)
    working["quality_tags_en"] = quality_tags
    working["negative_tags_en"] = negative_tags
    working["caption_short_en"] = caption_short
    working["caption_long_en"] = caption_long
    working["natural_language_en"] = natural_language
    if all_tags and "canonical_tags_en" not in working:
        working["canonical_tags_en"] = normalized_tags or translated_tags or all_tags
    if all_tags and "extended_tags_en" not in working:
        working["extended_tags_en"] = expanded_tags or all_tags
    return working


def build_newbie_xml_prompt(json_result):
    character_tags = json_result.get("character_tags_en", [])
    appearance_tags = json_result.get("appearance_tags_en", [])
    outfit_tags = json_result.get("outfit_tags_en", [])
    expression_tags = json_result.get("expression_tags_en", [])
    pose_tags = json_result.get("pose_tags_en", [])
    camera_tags = json_result.get("camera_tags_en", [])
    style_tags = json_result.get("style_tags_en", [])
    quality_tags = json_result.get("quality_tags_en", [])
    safety_tags = json_result.get("safety_tags_en", [])
    caption_text = str(json_result.get("caption_long_en", "") or json_result.get("natural_language_en", "")).strip()

    def join_tags(values):
        return ", ".join([str(item).strip() for item in values if str(item).strip()])

    lines = [
        "<character_1>",
        f"<identity>{join_tags(character_tags)}</identity>",
        f"<appearance>{join_tags(appearance_tags)}</appearance>",
        f"<outfit>{join_tags(outfit_tags)}</outfit>",
        f"<expression>{join_tags(expression_tags)}</expression>",
        f"<pose>{join_tags(pose_tags)}</pose>",
        "</character_1>",
        "<general_tags>",
        f"<camera>{join_tags(camera_tags)}</camera>",
        f"<style>{join_tags(style_tags)}</style>",
        f"<quality>{join_tags(quality_tags)}</quality>",
        f"<safety>{join_tags(safety_tags)}</safety>",
        "</general_tags>",
    ]
    if caption_text:
        lines.append(f"<caption>{caption_text}</caption>")
    return "\n".join(lines)


def build_training_two_line_text(tag_items, caption_text):
    first_line = ", ".join([str(item).strip() for item in tag_items if str(item).strip()]).strip()
    second_line = str(caption_text or "").strip()
    if first_line and second_line:
        return first_line + "\n\n" + second_line
    return first_line or second_line


def clean_training_passthrough_tag(tag):
    value = str(tag or "").strip()
    while value.endswith(","):
        value = value[:-1].strip()
    return value


def build_training_passthrough_tags(tags):
    # Training tag rows should preserve WD14 as the label source; LLM only supplements the NL caption.
    return [clean_training_passthrough_tag(tag) for tag in tags or [] if clean_training_passthrough_tag(tag)]


def get_training_base_tags(json_result, inputs):
    raw_tags = inputs.get("wd14_raw_tags_en", [])
    if isinstance(raw_tags, list) and raw_tags:
        return build_training_passthrough_tags(raw_tags)

    base_tags = json_result.get("extended_tags_en", []) or json_result.get("canonical_tags_en", []) or json_result.get("expanded_tags_en", []) or json_result.get("normalized_tags_en", [])
    return build_training_passthrough_tags(base_tags)


def has_custom_training_trigger(tags):
    return any(str(tag).strip().startswith("@") for tag in tags or [])


def build_resolved_character_block_keys(json_result):
    keys = set()
    for hit in json_result.get("resolved_character_tags", []) or []:
        if not isinstance(hit, dict):
            continue
        for field in ("canonical_tag", "matched_alias"):
            value = str(hit.get(field, "")).strip()
            if value:
                keys.add(normalize_alias_key(value))
        for value in hit.get("blocked_tags", []) or []:
            value = str(value).strip()
            if value:
                keys.add(normalize_alias_key(value))
    return keys


TRAINING_METADATA_TAG_KEYS = {
    normalize_alias_key(value)
    for value in (
        "artist name",
        "signature",
        "character name",
        "copyright name",
        "character signature",
        "twitter username",
        "username",
        "watermark",
        "english text",
        "copyright notice",
        "cover page",
        "doujin cover",
        "magazine cover",
        "cover art",
    )
}


def filter_training_character_pollution(tags, json_result):
    """Training tag rows are WD14 passthrough; do not let LLM/dictionary cleanup delete tags."""
    return build_training_passthrough_tags(tags)


def build_resolved_character_name_blocks(json_result):
    ambiguous = {
        "black", "blue", "brown", "green", "grey", "gray", "orange", "pink", "purple", "red", "white", "yellow",
        "ring", "dog", "cat", "hood", "jewelry", "belt", "cover", "patch", "rose", "star", "shadow",
    }
    names = set()
    for hit in json_result.get("resolved_character_tags", []) or []:
        if not isinstance(hit, dict):
            continue
        for field in ("canonical_tag", "matched_alias"):
            raw = str(hit.get(field, "")).strip()
            if not raw:
                continue
            base = re.split(r"[\(_]", raw.replace("\\", ""), maxsplit=1)[0]
            base = base.replace("_", " ").replace("-", " ").strip()
            if len(base) >= 4 and base.lower() not in ambiguous:
                names.add(base)
    return sorted(names, key=len, reverse=True)


def sanitize_training_caption_generic_identity(caption):
    cleaned = str(caption or "")
    if not cleaned:
        return cleaned

    # Avoid teaching the training set accidental IP/character recognition. Keep visible details instead.
    cleaned = re.sub(
        r"\b[A-Z][A-Za-z0-9_-]{1,24}\s+from\s+[A-Z][A-Za-z0-9&:.' -]{2,48}\s+is\s+depicted\b",
        "A solo anime girl is depicted",
        cleaned,
    )
    cleaned = re.sub(
        r"\b(?:This is|The image is|It is)\s+(?:a\s+)?(?:vibrant\s+|close-up\s+|dynamic\s+)*portrait of\s+[A-Z][A-Za-z0-9_-]{1,24}\s*,",
        "This is a portrait of a solo anime girl,",
        cleaned,
    )
    cleaned = re.sub(
        r"\b(?:depicts|shows|features)\s+[A-Z][A-Za-z0-9_-]{1,24}\s+from\s+[A-Z][A-Za-z0-9&:.' -]{2,48}\b",
        "shows a solo anime girl",
        cleaned,
    )
    cleaned = re.sub(
        r"\bfrom\s+(?:Azur Lane|Arknights|Genshin Impact|Honkai(?: Star Rail)?|Kantai Collection|Kancolle|Blue Archive|Fate(?:/Grand Order)?|Touhou|Pokemon|Ring Fit Adventure|Duck Hunt)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:Azur Lane|Arknights|Genshin Impact|Honkai(?: Star Rail)?|Kantai Collection|Kancolle|Blue Archive|Fate(?:/Grand Order)?|Touhou|Pokemon|Ring Fit Adventure|Duck Hunt)\b",
        "anime source",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(?:Z23|Atago|Ai)\b\s*,?\s*", "", cleaned)
    return cleaned


def sanitize_training_caption_character_pollution(caption, json_result):
    if not caption:
        return caption
    cleaned = sanitize_training_caption_generic_identity(caption)
    for name in build_resolved_character_name_blocks(json_result):
        escaped = re.escape(name)
        cleaned = re.sub(rf"\s*,\s*{escaped}\s*,\s*", ", ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(rf"\b{escaped}\b\s*,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\banime source\s+is depicted\b", "A solo anime girl is depicted", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r",\s*,", ",", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = cleaned.replace("girl, with", "girl with").replace("character, with", "character with")
    return cleaned.strip(" ,")


def build_training_caption_from_sections(json_result, fallback_tags):
    def clean_join(values, limit):
        return ", ".join([str(item).strip() for item in values[:limit] if str(item).strip()])

    character = clean_join(json_result.get("character_tags_en", []), 3)
    appearance = clean_join(json_result.get("appearance_tags_en", []), 8)
    outfit = clean_join(json_result.get("outfit_tags_en", []), 8)
    expression = clean_join(json_result.get("expression_tags_en", []), 4)
    pose = clean_join(json_result.get("pose_tags_en", []), 5)
    camera = clean_join(json_result.get("camera_tags_en", []), 4)
    style = clean_join(json_result.get("style_tags_en", []), 5)

    sentences = []
    subject = character or clean_join(fallback_tags, 4) or "an anime character"
    first_parts = [subject]
    if appearance:
        first_parts.append(appearance)
    sentences.append("The image shows " + ", ".join(first_parts) + ".")

    detail_parts = []
    if outfit:
        detail_parts.append("The outfit includes " + outfit)
    if expression:
        detail_parts.append("the expression reads as " + expression)
    if pose:
        detail_parts.append("the pose is described by " + pose)
    if detail_parts:
        sentences.append("; ".join(detail_parts) + ".")

    composition_parts = []
    if camera:
        composition_parts.append("Composition and camera cues include " + camera)
    if style:
        composition_parts.append("the visual style is " + style)
    if composition_parts:
        sentences.append("; ".join(composition_parts) + ".")

    if len(sentences) < 2 and fallback_tags:
        sentences.append("Additional visible elements include " + clean_join(fallback_tags[4:], 14) + ".")
    return compact_text(" ".join(sentences), 620)


def select_training_caption(json_result, training_tags):
    caption = (
        str(json_result.get("caption_long_en", "")).strip()
        or str(json_result.get("natural_language_en", "")).strip()
        or str(json_result.get("caption_short_en", "")).strip()
    )
    if len(caption) >= 180:
        return caption
    fallback_caption = build_training_caption_from_sections(json_result, training_tags)
    if not caption:
        return fallback_caption
    if fallback_caption and len(fallback_caption) > len(caption):
        return fallback_caption
    return caption


def profile_format_prompt(profile_name, json_result):
    normalized_tags = filter_globally_blocked_tags(strip_quality_control_tags(json_result.get("normalized_tags_en", [])))
    expanded_tags = filter_globally_blocked_tags(strip_quality_control_tags(json_result.get("expanded_tags_en", [])))
    quality_tags = dedupe_tags(json_result.get("quality_tags_en", []))
    negative_tags = filter_globally_blocked_tags(dedupe_tags(json_result.get("negative_tags_en", [])))
    translated_tags = filter_globally_blocked_tags(strip_quality_control_tags(json_result.get("tag_list", [])))
    natural_language_en = str(json_result.get("natural_language_en", "")).strip()
    caption_short_en = str(json_result.get("caption_short_en", "")).strip()
    caption_long_en = str(json_result.get("caption_long_en", "")).strip()
    training_base_tags = build_training_passthrough_tags(json_result.get("training_base_tags_en", []))

    base_positive = []
    if expanded_tags:
        base_positive.extend(expanded_tags)
    elif normalized_tags:
        base_positive.extend(normalized_tags)
    elif translated_tags:
        base_positive.extend(translated_tags)

    if profile_name == "anima_v1":
        positive_prefix = ["masterpiece", "best quality", "score_7", "safe"]
        negative_prefix = ["worst quality", "low quality", "score_1", "score_2", "score_3", "artist name"]
        positive_tags = dedupe_tags(positive_prefix + quality_tags + base_positive)
        negative_final = dedupe_tags(negative_prefix + negative_tags)
        formatted_tag_list = dedupe_tags(
            [normalize_tag_for_anima(tag) for tag in positive_tags if normalize_tag_for_anima(tag)]
        )
        formatted_tag_text = ", ".join(formatted_tag_list)
        inference_caption = natural_language_en or caption_long_en or caption_short_en
        if inference_caption:
            formatted_positive = build_training_two_line_text(formatted_tag_list, inference_caption)
        else:
            formatted_positive = formatted_tag_text
        formatted_negative = ", ".join(
            dedupe_tags([normalize_tag_for_anima(tag) for tag in negative_final if normalize_tag_for_anima(tag)])
        )
        return {
            "target_profile": "anima_v1",
            "formatted_prompt": formatted_positive,
            "formatted_prompt_tags": formatted_tag_text,
            "formatted_prompt_hybrid": formatted_positive,
            "formatted_negative_prompt": formatted_negative,
            "profile_notes_cn": (
                "按 Anima 混合范式格式化：第一段为 Danbooru tag 骨架并带推理质量词，"
                "空行后补一小段自然语言描述；小写 tag、下划线改空格（score tags 除外）。"
            ),
        }

    if profile_name == "illustrious_xl_v01":
        default_negative = [
            "worst quality", "comic", "multiple views", "bad quality", "low quality",
            "lowres", "displeasing", "very displeasing", "bad anatomy", "bad hands",
            "scan artifacts", "monochrome", "greyscale", "signature", "twitter username",
            "jpeg artifacts", "2koma", "4koma", "guro", "extra digits", "fewer digits"
        ]
        include_r18 = detect_r18_intent(base_positive + quality_tags + negative_tags + translated_tags + normalized_tags)
        quality_tail = build_quality_tail_for_illustrious_or_noobai(include_r18)
        base_final = [normalize_tag_for_illustrious(tag) for tag in dedupe_tags(base_positive)]
        quality_final = [normalize_tag_for_illustrious(tag) for tag in quality_tail]
        negative_final = dedupe_tags(default_negative + negative_tags)
        return {
            "target_profile": "illustrious_xl_v01",
            "formatted_prompt": ", ".join([tag for tag in base_final + quality_final if tag]),
            "formatted_negative_prompt": ", ".join([normalize_tag_for_illustrious(tag) for tag in negative_final if normalize_tag_for_illustrious(tag)]),
            "format_debug": {
                "profile": "illustrious_xl_v01",
                "include_r18_quality_suffix": include_r18
            },
            "profile_notes_cn": (
                "按 Illustrious XL v0.1 范式输出：使用 Danbooru tags，可保留下划线；"
                "完整质量词尾段固定放最后；仅在检测到 R18 意图时附加 NSFW 与 rating explicit。"
            ),
        }

    if profile_name == "noobai_xl_1_1":
        negative_prefix = [
            "worst quality", "old", "early", "low quality", "lowres",
            "signature", "username", "logo", "bad hands", "mutated hands",
            "mammal", "anthro", "furry", "ambiguous form", "feral", "semi-anthro"
        ]
        include_r18 = detect_r18_intent(base_positive + quality_tags + negative_tags + translated_tags + normalized_tags)
        quality_tail = build_quality_tail_for_illustrious_or_noobai(include_r18)
        base_final = [normalize_tag_for_noobai(tag) for tag in dedupe_tags(base_positive)]
        quality_final = [normalize_tag_for_noobai(tag) for tag in quality_tail]
        negative_final = dedupe_tags(negative_prefix + negative_tags)
        return {
            "target_profile": "noobai_xl_1_1",
            "formatted_prompt": ", ".join([tag for tag in base_final + quality_final if tag]),
            "formatted_negative_prompt": ", ".join([normalize_tag_for_noobai(tag) for tag in negative_final if normalize_tag_for_noobai(tag)]),
            "format_debug": {
                "profile": "noobai_xl_1_1",
                "include_r18_quality_suffix": include_r18
            },
            "profile_notes_cn": (
                "按 NoobAI XL 1.1 范式输出：使用 Danbooru tags，但不保留下划线；"
                "完整质量词尾段固定放最后；仅在检测到 R18 意图时附加 NSFW 与 rating explicit。"
            ),
        }

    if profile_name == "newbie_exp01":
        positive_tags = dedupe_tags(base_positive + quality_tags)
        negative_final = dedupe_tags(negative_tags)
        xml = build_newbie_xml_prompt(json_result)
        return {
            "target_profile": "newbie_exp01",
            "formatted_prompt": xml,
            "formatted_negative_prompt": ", ".join(negative_final),
            "formatted_prompt_xml": xml,
            "profile_notes_cn": (
                "NewBie-image Exp0.1 输出为结构化 XML。当前会按角色、外观、服装、表情、姿态、镜头、风格、质量分区组织。"
            ),
        }

    if profile_name == "flux_natural_language_v1":
        final_caption = caption_long_en or natural_language_en or caption_short_en
        if not final_caption:
            final_caption = compact_text(", ".join(base_positive[:24]), 260)
        return {
            "target_profile": "flux_natural_language_v1",
            "formatted_prompt": final_caption,
            "formatted_negative_prompt": ", ".join(negative_tags),
            "formatted_prompt_caption": final_caption,
            "profile_notes_cn": "按纯自然语言 caption 输出，适合 Flux / 纯自然语言打标链路。",
        }

    if profile_name == "structured_json_v1":
        payload = {
            "canonical_tags_en": json_result.get("canonical_tags_en", normalized_tags or translated_tags),
            "extended_tags_en": json_result.get("extended_tags_en", expanded_tags or base_positive),
            "character_tags_en": json_result.get("character_tags_en", []),
            "appearance_tags_en": json_result.get("appearance_tags_en", []),
            "outfit_tags_en": json_result.get("outfit_tags_en", []),
            "expression_tags_en": json_result.get("expression_tags_en", []),
            "pose_tags_en": json_result.get("pose_tags_en", []),
            "camera_tags_en": json_result.get("camera_tags_en", []),
            "style_tags_en": json_result.get("style_tags_en", []),
            "quality_tags_en": quality_tags,
            "negative_tags_en": negative_tags,
            "caption_short_en": caption_short_en,
            "caption_long_en": caption_long_en or natural_language_en,
        }
        compact_json = json.dumps(payload, ensure_ascii=False)
        return {
            "target_profile": "structured_json_v1",
            "formatted_prompt": compact_json,
            "formatted_negative_prompt": ", ".join(negative_tags),
            "formatted_prompt_json": compact_json,
            "profile_notes_cn": "按结构化 JSON 标注输出，适合 JSON 打标模型或后处理流程。",
        }

    if profile_name == "anima_train_v1":
        tag_source = training_base_tags or dedupe_tags(base_positive)
        tag_source = filter_training_character_pollution(tag_source, json_result)
        training_tags = build_training_passthrough_tags(tag_source)
        training_caption = select_training_caption(json_result, training_tags)
        training_caption = sanitize_training_caption_character_pollution(training_caption, json_result)
        training_text = build_training_two_line_text(training_tags, training_caption)
        return {
            "target_profile": "anima_train_v1",
            "formatted_prompt": training_text,
            "formatted_negative_prompt": "",
            "formatted_prompt_tags": ", ".join(training_tags),
            "formatted_prompt_caption": training_caption,
            "formatted_training_text": training_text,
            "profile_notes_cn": "Anima 训练标注格式：第一段完全保留 WD14 tags，空一行后第二段为 LLM 补充自然语言；不添加质量词和负面词。",
        }

    if profile_name == "illustrious_train_v1":
        tag_source = training_base_tags or dedupe_tags(base_positive)
        tag_source = filter_training_character_pollution(tag_source, json_result)
        training_tags = build_training_passthrough_tags(tag_source)
        training_caption = select_training_caption(json_result, training_tags)
        training_caption = sanitize_training_caption_character_pollution(training_caption, json_result)
        training_text = build_training_two_line_text(training_tags, training_caption)
        return {
            "target_profile": "illustrious_train_v1",
            "formatted_prompt": training_text,
            "formatted_negative_prompt": "",
            "formatted_prompt_tags": ", ".join(training_tags),
            "formatted_prompt_caption": training_caption,
            "formatted_training_text": training_text,
            "profile_notes_cn": "Illustrious 训练标注格式：第一段完全保留 WD14 tags，空一行后第二段为 LLM 补充自然语言；不追加质量词和负面词。",
        }

    if profile_name == "noobai_train_v1":
        tag_source = training_base_tags or dedupe_tags(base_positive)
        tag_source = filter_training_character_pollution(tag_source, json_result)
        training_tags = build_training_passthrough_tags(tag_source)
        training_caption = select_training_caption(json_result, training_tags)
        training_caption = sanitize_training_caption_character_pollution(training_caption, json_result)
        training_text = build_training_two_line_text(training_tags, training_caption)
        return {
            "target_profile": "noobai_train_v1",
            "formatted_prompt": training_text,
            "formatted_negative_prompt": "",
            "formatted_prompt_tags": ", ".join(training_tags),
            "formatted_prompt_caption": training_caption,
            "formatted_training_text": training_text,
            "profile_notes_cn": "NoobAI 训练标注格式：第一段完全保留 WD14 tags，空一行后第二段为 LLM 补充自然语言；不追加质量词和负面词。",
        }

    if profile_name == "flux_train_nl_v1":
        training_caption = caption_long_en or natural_language_en or caption_short_en
        if not training_caption:
            training_caption = compact_text(", ".join(base_positive[:24]), 260)
        return {
            "target_profile": "flux_train_nl_v1",
            "formatted_prompt": training_caption,
            "formatted_negative_prompt": "",
            "formatted_prompt_caption": training_caption,
            "formatted_training_text": training_caption,
            "profile_notes_cn": "Flux 训练标注格式：只输出自然语言描述，不输出 tags、质量词和负面词。",
        }

    if profile_name == "newbie_train_xml_v1":
        xml = build_newbie_xml_prompt(json_result)
        return {
            "target_profile": "newbie_train_xml_v1",
            "formatted_prompt": xml,
            "formatted_negative_prompt": "",
            "formatted_prompt_xml": xml,
            "formatted_training_text": xml,
            "profile_notes_cn": "NewBie 训练标注格式：只输出结构化 XML，不追加质量词和负面词。",
        }

    if profile_name == "structured_json_train_v1":
        payload = {
            "canonical_tags_en": json_result.get("canonical_tags_en", normalized_tags or translated_tags),
            "extended_tags_en": json_result.get("extended_tags_en", expanded_tags or base_positive),
            "character_tags_en": json_result.get("character_tags_en", []),
            "appearance_tags_en": json_result.get("appearance_tags_en", []),
            "outfit_tags_en": json_result.get("outfit_tags_en", []),
            "expression_tags_en": json_result.get("expression_tags_en", []),
            "pose_tags_en": json_result.get("pose_tags_en", []),
            "camera_tags_en": json_result.get("camera_tags_en", []),
            "style_tags_en": json_result.get("style_tags_en", []),
            "caption_short_en": caption_short_en,
            "caption_long_en": caption_long_en or natural_language_en,
        }
        compact_json = json.dumps(payload, ensure_ascii=False)
        return {
            "target_profile": "structured_json_train_v1",
            "formatted_prompt": compact_json,
            "formatted_negative_prompt": "",
            "formatted_prompt_json": compact_json,
            "formatted_training_text": compact_json,
            "profile_notes_cn": "结构化 JSON 训练标注格式：只保留训练所需字段，不追加质量词和负面词。",
        }

    positive_tags = dedupe_tags(quality_tags + base_positive)
    negative_final = dedupe_tags(negative_tags)
    return {
        "target_profile": profile_name or "generic_tag_model",
        "formatted_prompt": ", ".join(positive_tags),
        "formatted_negative_prompt": ", ".join(negative_final),
        "profile_notes_cn": "按通用 tag 模型格式输出，未应用特定模型专属排序规则。",
    }


def enrich_json_result(task_type, json_result, inputs):
    supported = {
        "expand_anime_tags",
        "translate_anime_tags",
        "normalize_anime_tags",
        "generate_outfit_tags",
        "extract_tags_from_image",
        "vision_tagging",
        "image_captioning",
        "refine_wd14_tags",
        "generate_natural_caption",
    }
    if task_type not in supported:
        return json_result

    enriched = dict(json_result or {})
    for carry_key in ("natural_language_en", "caption_short_en", "caption_long_en"):
        if not str(enriched.get(carry_key, "")).strip() and str(inputs.get(carry_key, "")).strip():
            enriched[carry_key] = str(inputs.get(carry_key, "")).strip()
    enriched = apply_character_aliases_to_result(task_type, enriched, inputs)
    enriched = infer_structured_tag_sections(enriched, inputs)
    dictionary_suggestions = dictionary_suggested_tags_for_inputs(inputs)
    if dictionary_suggestions:
        enriched["dictionary_suggested_tags_en"] = dictionary_suggestions
        if task_type in {"expand_anime_tags", "normalize_anime_tags", "translate_anime_tags"}:
            expanded_now = dedupe_tags(enriched.get("expanded_tags_en", []) or enriched.get("normalized_tags_en", []))
            if len(expanded_now) < 24:
                enriched["expanded_tags_en"] = dedupe_tags(expanded_now + dictionary_suggestions)[:48]
    if inputs.get("wd14_raw_tags_en"):
        enriched["wd14_raw_tags_en"] = list(inputs.get("wd14_raw_tags_en", []))
        enriched["training_base_tags_en"] = get_training_base_tags(enriched, inputs)
    target_profile = inputs.get("target_profile", "generic_tag_model")
    enriched.update(profile_format_prompt(target_profile, enriched))
    return enriched


def build_outfit_post_expand_inputs(inputs, outfit_json):
    candidate_tags = [
        item.get("tag", "")
        for item in outfit_json.get("retrieved_clothing_candidates", [])
        if str(item.get("tag", "")).strip()
    ]
    seed_tags = dedupe_tags(
        strip_quality_control_tags(outfit_json.get("style_tags_en", []))
        + strip_quality_control_tags(outfit_json.get("detail_tags_en", []))
        + strip_quality_control_tags(outfit_json.get("normalized_tags_en", []))
        + strip_quality_control_tags(outfit_json.get("expanded_tags_en", []))
        + candidate_tags[:8]
    )
    style_hint_parts = [
        str(inputs.get("style_direction", "")).strip(),
        str(inputs.get("personality_traits", "")).strip(),
        str(inputs.get("outfit_scene", "")).strip(),
        str(outfit_json.get("outfit_direction_cn", "")).strip(),
        str(outfit_json.get("selected_structure_cn", "")).strip(),
    ]
    return {
        "raw_tags": ", ".join([tag for tag in seed_tags if tag]),
        "style_hint": " | ".join([part for part in style_hint_parts if part]),
        "purpose": "补全服装出图 tags，补足细节与搭配信息，避免重复质量词，长度适合实际出图。",
        "target_profile": inputs.get("target_profile", "generic_tag_model"),
        "resolved_character_tags": inputs.get("resolved_character_tags", []),
    }


def merge_outfit_expansion_result(base_result, expansion_result, inputs):
    merged = dict(base_result)
    base_style = strip_quality_control_tags(base_result.get("style_tags_en", []))
    base_detail = strip_quality_control_tags(base_result.get("detail_tags_en", []))
    base_normalized = strip_quality_control_tags(base_result.get("normalized_tags_en", []))
    base_expanded = strip_quality_control_tags(base_result.get("expanded_tags_en", []))
    expansion_normalized = strip_quality_control_tags(expansion_result.get("normalized_tags_en", []))
    expansion_expanded = strip_quality_control_tags(expansion_result.get("expanded_tags_en", []))
    candidate_tags = [
        item.get("tag", "")
        for item in base_result.get("retrieved_clothing_candidates", [])
        if str(item.get("tag", "")).strip()
    ]

    merged["normalized_tags_en"] = dedupe_tags(base_normalized + expansion_normalized)
    merged["expanded_tags_en"] = dedupe_tags(
        base_style
        + base_detail
        + base_expanded
        + expansion_expanded
        + candidate_tags[:10]
    )
    merged["quality_tags_en"] = dedupe_tags(base_result.get("quality_tags_en", []) + expansion_result.get("quality_tags_en", []))
    merged["negative_tags_en"] = dedupe_tags(base_result.get("negative_tags_en", []) + expansion_result.get("negative_tags_en", []))
    merged["post_expand_status"] = "applied"
    merged["post_expand_notes_cn"] = expansion_result.get("notes_cn", "")
    return apply_character_aliases_to_result("generate_outfit_tags", merged, inputs)


def fallback_json_result(task_type, raw_text, inputs, parse_error):
    note = f"模型未按 JSON 输出，已使用纯文本回退解析。parse_error={parse_error}"
    if task_type == "expand_anime_tags":
        source_tags = split_tag_like_text(inputs.get("raw_tags", ""))
        generated_tags = split_tag_like_text(raw_text)
        merged = []
        seen = set()
        for item in source_tags + generated_tags:
            lowered = item.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            merged.append(item)
        return {
            "normalized_tags_en": source_tags[:24],
            "expanded_tags_en": merged[:48],
            "natural_language_en": "",
            "quality_tags_en": [
                "masterpiece",
                "best quality",
                "highly detailed",
                "clean lineart",
            ],
            "negative_tags_en": [
                "low quality",
                "bad anatomy",
                "extra fingers",
                "blurry",
                "cropped",
                "oversaturated",
            ],
            "notes_cn": note,
        }
    if task_type == "translate_anime_tags":
        tag_list = split_tag_like_text(raw_text)
        return {
            "direction": inputs.get("direction", "zh_to_en_tags"),
            "source_text": inputs.get("raw_text", ""),
            "translated_text": raw_text.strip(),
            "tag_list": tag_list[:48],
            "notes_cn": note,
        }
    if task_type == "normalize_anime_tags":
        source_tags = split_tag_like_text(inputs.get("raw_tags", ""))
        normalized = []
        seen = set()
        for item in source_tags:
            lowered = item.lower().strip()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(lowered)
        return {
            "normalized_tags_en": normalized[:48],
            "removed_or_merged_items": [],
            "recommended_prompt_order_en": normalized[:48],
            "negative_tags_en": [
                "low quality",
                "worst quality",
                "bad anatomy",
                "extra fingers",
                "blurry",
                "cropped",
            ],
            "notes_cn": note,
        }
    if task_type == "generate_outfit_tags":
        candidate_tags = [item["record"].get("tag", "") for item in inputs.get("retrieved_clothing_candidates", [])[:12]]
        candidate_tags = [tag for tag in dedupe_tags(candidate_tags) if tag]
        return {
            "outfit_direction_cn": "模型未按 JSON 输出，已按候选服装 tag 做回退。",
            "selected_structure_cn": "回退模式：由候选单品自动组合",
            "style_tags_en": [],
            "detail_tags_en": [],
            "normalized_tags_en": candidate_tags[:8],
            "expanded_tags_en": candidate_tags[:12],
            "quality_tags_en": [
                "masterpiece",
                "best quality",
                "high resolution",
            ],
            "negative_tags_en": [
                "low quality",
                "worst quality",
                "bad anatomy",
                "blurry",
                "realistic",
            ],
            "selected_clothing_notes_cn": [],
            "notes_cn": note,
        }
    if task_type in ("extract_tags_from_image", "vision_tagging", "image_captioning", "refine_wd14_tags"):
        raw_tag_input = str(inputs.get("raw_tags", "") or "").strip()
        source_tags = split_tag_like_text(raw_tag_input)
        if looks_like_jsonish_output(raw_text):
            normalized_from_jsonish = extract_jsonish_string_array(raw_text, "normalized_tags_en")
            expanded_from_jsonish = extract_jsonish_string_array(raw_text, "expanded_tags_en")
            source_tags = source_tags or normalized_from_jsonish
            merged_tags = dedupe_tags(source_tags + expanded_from_jsonish)
        else:
            merged_tags = dedupe_tags(source_tags + split_tag_like_text(raw_text))
        caption = (
            extract_jsonish_string_field(raw_text, "caption_short_en", 160)
            or extract_jsonish_string_field(raw_text, "natural_language_en", 220)
            or compact_text(", ".join(merged_tags[:18]), 220)
        )
        return {
            "normalized_tags_en": source_tags[:32],
            "expanded_tags_en": merged_tags[:48],
            "character_tags_en": inputs.get("resolved_character_tag_strings", []),
            "appearance_tags_en": merged_tags[:20],
            "outfit_tags_en": [],
            "expression_tags_en": [],
            "pose_tags_en": [],
            "camera_tags_en": [],
            "style_tags_en": [],
            "quality_tags_en": ["masterpiece", "best quality"],
            "negative_tags_en": ["low quality", "blurry", "bad anatomy"],
            "caption_short_en": caption,
            "caption_long_en": caption,
            "natural_language_en": caption,
            "notes_cn": note,
        }
    if task_type == "generate_natural_caption":
        source_tags = split_tag_like_text(inputs.get("raw_tags", "") or inputs.get("raw_text", ""))
        if looks_like_jsonish_output(raw_text):
            base_subject = ", ".join(source_tags[:12])
            caption = compact_text(f"anime illustration featuring {base_subject}", 220) if base_subject else ""
        else:
            caption = raw_text.strip() or compact_text(", ".join(source_tags[:20]), 220)
        return {
            "caption_short_en": compact_text(caption, 160),
            "caption_long_en": caption,
            "natural_language_en": caption,
            "notes_cn": note,
        }
    return {
        "fallback_text": raw_text.strip(),
        "notes_cn": note,
    }


def build_character_design_prompts(inputs):
    system_prompt = (
        "You are an expert anime character concept planner for game production. "
        "Work like a senior character designer, not a casual chatbot. "
        "You must think in terms of anime readability, silhouette, role fantasy, "
        "expression range, outfit logic, color contrast, and downstream image generation stability. "
        "Output only one JSON object with no markdown."
    )

    user_prompt = f"""
请为一个二次元角色生成成熟的人设设计方案，并输出严格 JSON。

任务目标：
- 设计一个适合后续立绘、表情差分、阶段A六宫格参考图、训练数据生产的人物
- 兼顾二次元角色辨识度、绘图 prompt 可执行性、后续多表情延展能力
- 输出中同时保留中文设计说明与英文绘图 prompt/tag

项目背景：
{inputs.get("project_background", "").strip()}

题材/类型：
{inputs.get("genre", "").strip()}

目标受众：
{inputs.get("target_audience", "").strip()}

视觉风格：
{inputs.get("visual_style", "").strip()}

角色原型：
{inputs.get("role_archetype", "").strip()}

约束：
{inputs.get("constraints", "").strip()}

补充参考：
{inputs.get("reference_notes", "").strip()}

请严格输出一个 JSON 对象，字段必须包含：
{{
  "character_name_suggestion": "string",
  "core_concept_cn": "string",
  "role_summary_cn": "string",
  "personality_traits_cn": ["string", "string", "string"],
  "appearance_design_cn": {{
    "hair": "string",
    "face": "string",
    "body_impression": "string",
    "outfit": "string",
    "palette": "string"
  }},
  "expression_tendency_cn": ["string", "string", "string", "string"],
  "drawing_prompt_en": "string",
  "tag_list_en": ["string", "string", "string"],
  "negative_tags_en": ["string", "string", "string"],
  "audit_notes_cn": "string"
}}

硬性要求：
- 保持二次元角色设计思维，不要写成现实主义摄影提示
- tag_list_en 用英文短 tag，适合后续给绘图模型理解
- tag_list_en 尽量覆盖角色、外观、服装、表情、姿态、镜头、背景、风格，不要只给极短列表
- 如果 Additional Runtime Context 里有 Tag Frequency 或 Cooccurrence Suggestions，优先参考其中的 Danbooru 拼写和高相关候选
- negative_tags_en 重点压制崩坏、脏乱、跑题、过度性感和不适合立绘的内容
- 输出必须是 JSON，不要加解释，不要加 markdown 代码块
""".strip()

    return system_prompt, user_prompt


def build_expand_tags_prompts(inputs):
    raw_tags = inputs.get("raw_tags", "").strip()
    style_hint = inputs.get("style_hint", "").strip()
    purpose = inputs.get("purpose", "").strip()
    target_profile = inputs.get("target_profile", "generic_tag_model").strip()
    prompt_mode = str(inputs.get("prompt_mode", "")).strip()
    alias_reference = build_alias_reference_text(inputs.get("resolved_character_tags", []))
    system_prompt = (
        "You are an anime image prompt assistant. "
        "Expand short tag seeds into richer, cleaner generation tags while preserving intent. "
        "Output only one JSON object with no markdown, no prose, and no prefix text. "
        "Your first character must be { and your last character must be }."
    )
    if target_profile == "anima_v1" or prompt_mode == "anima_hybrid":
        user_prompt = f"""
请对这组二次元绘图输入做扩写与整理，并严格输出 JSON。

原始 tags：
{raw_tags}

风格提示：
{style_hint}

用途：
{purpose}

目标模型范式：
{target_profile}

角色规范 tag 参考：
{alias_reference or "无"}

请输出一个 JSON 对象，字段必须包含：
{{
  "normalized_tags_en": ["string", "string"],
  "expanded_tags_en": ["string", "string"],
  "natural_language_en": "string",
  "quality_tags_en": ["string", "string"],
  "negative_tags_en": ["string", "string"],
  "notes_cn": "string"
}}

要求：
- 保持二次元绘图语境
- normalized_tags_en 用清洗后的核心 Danbooru tags
- expanded_tags_en 在不跑题的前提下补充可执行 Danbooru tags 细节
- expanded_tags_en 尽量给出 24 到 48 个有用 tag；如果输入信息不足，也要补足构图、表情、姿态、服装、场景、风格等常见 Danbooru 维度
- natural_language_en 必须是一小段简洁的英文自然语言补充，用来补足 tag 难以表达的气质、构图或视觉感觉
- 最终风格是 Danbooru tag 骨架 + 短自然语言补充，不要只输出纯 tag 思路
- quality_tags_en 只放高质量和画面控制相关项
- negative_tags_en 压制常见崩坏
- 如果角色规范 tag 参考不为空，必须保留这些 canonical Danbooru tags，不要把角色名自行翻译成普通英文词
- 如果 Additional Runtime Context 里有 Tag Frequency 或 Cooccurrence Suggestions，要优先用它们修正 tag 拼写、补充高相关 tag，但不要加入和用户意图冲突的 tag
- 不要凭空加入 fetish、sexual、explicit、nsfw、nude 等内容，除非原始输入明确要求
- natural_language_en 不要重复罗列 tags，不要写成长段句子，控制在 8 到 24 个英文词左右
- 输出必须是 JSON，不要 markdown，不要解释性前言
- 你的输出第一字符必须是 {{，最后字符必须是 }}
""".strip()
        return system_prompt, user_prompt

    user_prompt = f"""
请对这组二次元绘图 tags 做扩写与整理，并严格输出 JSON。

原始 tags：
{raw_tags}

风格提示：
{style_hint}

用途：
{purpose}

目标模型范式：
{target_profile}

角色规范 tag 参考：
{alias_reference or "无"}

请输出一个 JSON 对象，字段必须包含：
{{
  "normalized_tags_en": ["string", "string"],
  "expanded_tags_en": ["string", "string"],
  "quality_tags_en": ["string", "string"],
  "negative_tags_en": ["string", "string"],
  "notes_cn": "string"
}}

要求：
- 保持二次元绘图语境
- normalized_tags_en 用清洗后的核心 tags
- expanded_tags_en 在不跑题的前提下补充可执行细节
- expanded_tags_en 尽量给出 24 到 48 个有用 tag；如果输入信息不足，也要补足构图、表情、姿态、服装、场景、风格等常见 Danbooru 维度
- quality_tags_en 只放高质量和画面控制相关项
- negative_tags_en 压制常见崩坏
- 如果角色规范 tag 参考不为空，必须保留这些 canonical Danbooru tags，不要把角色名自行翻译成普通英文词
- 如果 Additional Runtime Context 里有 Tag Frequency 或 Cooccurrence Suggestions，要优先用它们修正 tag 拼写、补充高相关 tag，但不要加入和用户意图冲突的 tag
- 不要凭空加入 fetish、sexual、explicit、nsfw、nude 等内容，除非原始输入明确要求
- 输出必须是 JSON，不要 markdown，不要解释性前言
- 你的输出第一字符必须是 {{，最后字符必须是 }}
""".strip()
    return system_prompt, user_prompt


def build_translate_tags_prompts(inputs):
    raw_text = inputs.get("raw_text", "").strip()
    direction = inputs.get("direction", "zh_to_en_tags").strip()
    target_profile = inputs.get("target_profile", "generic_tag_model").strip()
    alias_reference = build_alias_reference_text(inputs.get("resolved_character_tags", []))
    system_prompt = (
        "You are an anime tag translation assistant. "
        "Translate between Chinese descriptive tag phrases and compact English drawing tags. "
        "Output only one JSON object with no markdown, no prose, and no prefix text. "
        "Your first character must be { and your last character must be }."
    )
    user_prompt = f"""
请做二次元绘图 tag 翻译，并严格输出 JSON。

输入文本：
{raw_text}

方向：
{direction}

目标模型范式：
{target_profile}

角色规范 tag 参考：
{alias_reference or "无"}

请输出一个 JSON 对象，字段必须包含：
{{
  "direction": "{direction}",
  "source_text": "string",
  "translated_text": "string",
  "tag_list": ["string", "string"],
  "notes_cn": "string"
}}

要求：
- 如果是中文转英文，translated_text 要偏绘图 prompt/tag 风格
- 如果是英文转中文，保留二次元语义，不要翻得过于口语
- tag_list 尽量拆成清晰短 tag
- 如果角色规范 tag 参考不为空，涉及这些角色时必须直接使用规范 Danbooru tag，不要翻译成普通英文词
- 输出必须是 JSON，不要 markdown，不要前言
- 你的输出第一字符必须是 {{，最后字符必须是 }}
""".strip()
    return system_prompt, user_prompt


def build_normalize_tags_prompts(inputs):
    raw_tags = inputs.get("raw_tags", "").strip()
    target_profile = inputs.get("target_profile", "generic_tag_model").strip()
    alias_reference = build_alias_reference_text(inputs.get("resolved_character_tags", []))
    system_prompt = (
        "You are an anime tag normalization assistant. "
        "Clean, deduplicate, and standardize anime drawing tags. "
        "Output only one JSON object with no markdown, no prose, and no prefix text. "
        "Your first character must be { and your last character must be }."
    )
    user_prompt = f"""
请清洗并标准化这组二次元绘图 tags，并严格输出 JSON。

原始 tags：
{raw_tags}

目标模型范式：
{target_profile}

角色规范 tag 参考：
{alias_reference or "无"}

请输出一个 JSON 对象，字段必须包含：
{{
  "normalized_tags_en": ["string", "string"],
  "removed_or_merged_items": ["string", "string"],
  "recommended_prompt_order_en": ["string", "string"],
  "negative_tags_en": ["string", "string"],
  "notes_cn": "string"
}}

要求：
- 去重
- 合并明显重复或近义的 tag
- 尽量规范成适合二次元绘图模型理解的英文短 tag
- recommended_prompt_order_en 按更合理的 prompt 顺序给出
- negative_tags_en 补常见崩坏抑制项
- 如果角色规范 tag 参考不为空，必须优先使用这些 canonical Danbooru tags
- 如果 Additional Runtime Context 里有 Tag Frequency 或 Cooccurrence Suggestions，要用它们修正拼写和补充合理高相关 tag
- normalized_tags_en 和 recommended_prompt_order_en 不要过短；绘图用至少保留 18 个左右的有效 tag，训练/打标用优先保留 WD14 原标
- 不要添加用户没有要求的 fetish、sexual、explicit、nsfw、nude 等内容
- 输出必须是 JSON，不要 markdown，不要前言
- 你的输出第一字符必须是 {{，最后字符必须是 }}
""".strip()
    return system_prompt, user_prompt


def build_visual_tagging_prompts(inputs):
    wd14_tags = inputs.get("raw_tags", "").strip()
    style_hint = inputs.get("style_hint", "").strip()
    purpose = inputs.get("purpose", "").strip()
    target_profile = inputs.get("target_profile", "generic_tag_model").strip()
    alias_reference = build_alias_reference_text(inputs.get("resolved_character_tags", []))
    system_prompt = (
        "You are a precise anime image tagging assistant. "
        "Inspect the provided image directly. When WD14 tags are also provided, treat them as noisy hints rather than ground truth. "
        "Return one strict JSON object only. No markdown, no explanations, no prose outside JSON."
    )
    user_prompt = f"""
请直接观察这张二次元图片并输出结构化打标 JSON。

如果同时给了 WD14 tags，请把它们当作参考输入，不要盲目照抄。

WD14 / 参考 tags：
{wd14_tags or "无"}

风格提示：
{style_hint}

用途：
{purpose}

目标输出范式：
{target_profile}

角色规范 tag 参考：
{alias_reference or "无"}

请输出一个 JSON 对象，字段至少包含：
{{
  "normalized_tags_en": ["string"],
  "expanded_tags_en": ["string"],
  "character_tags_en": ["string"],
  "appearance_tags_en": ["string"],
  "outfit_tags_en": ["string"],
  "expression_tags_en": ["string"],
  "pose_tags_en": ["string"],
  "camera_tags_en": ["string"],
  "style_tags_en": ["string"],
  "quality_tags_en": ["string"],
  "negative_tags_en": ["string"],
  "caption_short_en": "string",
  "caption_long_en": "string",
  "natural_language_en": "string",
  "notes_cn": "string"
}}

要求：
- 基于图像内容判断，不要仅复述 WD14
- tag 以 Danbooru / anime drawing tags 为主
- natural_language_en / caption_long_en 要比 tag 更自然，但不要写成摄影散文
- 如果角色规范 tag 参考不为空，涉及这些角色时优先使用 canonical Danbooru tag
- 输出必须是一个 JSON 对象，首字符是 {{，尾字符是 }}
""".strip()
    return system_prompt, user_prompt


def build_refine_wd14_prompts(inputs):
    wd14_tags = inputs.get("raw_tags", "").strip()
    style_hint = inputs.get("style_hint", "").strip()
    purpose = inputs.get("purpose", "").strip()
    target_profile = inputs.get("target_profile", "generic_tag_model").strip()
    alias_reference = build_alias_reference_text(inputs.get("resolved_character_tags", []))
    system_prompt = (
        "You are an anime tag cleanup assistant specialized in WD14-assisted labeling. "
        "Use the provided image and WD14 tags together. "
        "Resolve obvious noise, repair canonical Danbooru tags, and add concise natural-language support text. "
        "Output one JSON object only."
    )
    user_prompt = f"""
请把这组 WD14 打标结果整理成更适合二次元生产和训练的结构化 JSON。

WD14 原始 tags：
{wd14_tags}

风格提示：
{style_hint}

用途：
{purpose}

目标输出范式：
{target_profile}

角色规范 tag 参考：
{alias_reference or "无"}

要求：
- 以 WD14 原始标签为主，不要大幅重写成另一套标签体系
- wd14_tags_raw 必须完整保留 WD14 原始标签顺序，除非是空项或完全重复项
- normalized_tags_en 应保留 WD14 中可靠的主体、角色、外观、服装、表情、姿态、构图标签，不要压缩成很短的一组摘要
- expanded_tags_en 只用于补充少量图像可确认的缺失标签，不要替代 WD14 原标
- 去掉明显噪声、重复、错误归类
- 训练/打标用途下，不要猜测或写出现有版权角色名、作品名、IP 名；只描述可见外观、服装、表情、姿态和构图
- caption_long_en / natural_language_en 写 2-4 句英文训练 caption，覆盖主体、外观、服装、表情、姿态、镜头/构图和显著细节
- caption 不要加入 masterpiece、best quality、score、negative prompt 这类质量词
- 如果图片能帮助判断，请优先依据图片修正 WD14

请输出一个 JSON 对象，字段至少包含：
{{
  "wd14_tags_raw": ["string"],
  "normalized_tags_en": ["string"],
  "expanded_tags_en": ["string"],
  "character_tags_en": ["string"],
  "appearance_tags_en": ["string"],
  "outfit_tags_en": ["string"],
  "expression_tags_en": ["string"],
  "pose_tags_en": ["string"],
  "camera_tags_en": ["string"],
  "style_tags_en": ["string"],
  "quality_tags_en": ["string"],
  "negative_tags_en": ["string"],
  "caption_short_en": "string",
  "caption_long_en": "string",
  "natural_language_en": "string",
  "notes_cn": "string"
}}
""".strip()
    return system_prompt, user_prompt


def build_natural_caption_prompts(inputs):
    raw_tags = inputs.get("raw_tags", "").strip()
    raw_text = inputs.get("raw_text", "").strip()
    purpose = inputs.get("purpose", "").strip()
    target_profile = inputs.get("target_profile", "flux_natural_language_v1").strip()
    system_prompt = (
        "You are an anime image caption writer. "
        "Use tags and the image together, then write clean generation-friendly natural language. "
        "Return one strict JSON object only."
    )
    user_prompt = f"""
请为这张图生成更自然的二次元英文描述，并严格输出 JSON。

参考 tags：
{raw_tags or raw_text or "无"}

用途：
{purpose}

目标输出范式：
{target_profile}

请输出一个 JSON 对象，字段至少包含：
{{
  "caption_short_en": "string",
  "caption_long_en": "string",
  "natural_language_en": "string",
  "notes_cn": "string"
}}

要求：
- 以现有 tags 为主要事实依据来写自然语言
- 不要重新发明一套和 tags 不一致的内容
- caption_short_en 用一句话概括
- caption_long_en 用 2-4 句英文，覆盖主体、外观、服装、表情、姿态、镜头/构图和显著细节
- natural_language_en 可以与 caption_long_en 接近，但要保持清晰、稳定、便于模型理解
- 不要写成小说，不要使用过度修辞
- 不要重复输出完整 tags 列表
- 如果目标是训练标注，不要猜测或写出现有版权角色名、作品名、IP 名；只描述图像中可见内容
- 如果目标是训练标注，不要加入 masterpiece、best quality、score、negative prompt 这类质量词
- 只输出一个很短的 JSON，不要在 JSON 之外添加任何内容
- 输出必须是 JSON
""".strip()
    return system_prompt, user_prompt


def build_outfit_generation_prompts(inputs):
    character_description = inputs.get("character_description", "").strip()
    personality_traits = inputs.get("personality_traits", "").strip()
    outfit_scene = inputs.get("outfit_scene", "").strip()
    season = inputs.get("season", "").strip()
    style_direction = inputs.get("style_direction", "").strip()
    design_constraints = inputs.get("design_constraints", "").strip()
    reference_notes = inputs.get("reference_notes", "").strip()
    target_profile = inputs.get("target_profile", "generic_tag_model").strip()
    clothing_query = "\n".join(
        [
            character_description,
            personality_traits,
            outfit_scene,
            season,
            style_direction,
            design_constraints,
            reference_notes,
        ]
    )
    outfit_context = infer_outfit_context(inputs)
    raw_candidates = search_clothing_records(clothing_query, limit=120, context=outfit_context)
    fallback_candidates = build_fallback_clothing_candidates(outfit_context)
    merged_candidates = merge_candidate_items(raw_candidates, fallback_candidates)
    clothing_candidates, slot_map = select_balanced_clothing_candidates(merged_candidates, context=outfit_context)
    candidate_block = format_structured_clothing_candidates(slot_map) or "无"
    outfit_structures = [
        "单层上衣 + 下装（不要外搭）",
        "衬衫/上衣 + 背心（轻叠穿） + 下装",
        "内搭 + 短外套/轻外层 + 下装",
        "连衣裙 / 背带裙 / 吊带裙一体式方案",
        "T恤/衬衫 + 长裤/牛仔裤的偏常服方案",
    ]

    system_prompt = (
        "You are an anime outfit prompt designer. "
        "You must produce Danbooru-oriented outfit tags for stylized anime character generation, "
        "not realistic fashion prose. Use the retrieved clothing knowledge as a controlled reference, "
        "but do not copy all candidates blindly. Output only one JSON object with no markdown. "
        "Your first character must be { and your last character must be }."
    )

    user_prompt = f"""
请根据角色信息生成适合二次元立绘的服装 tag 方案，并严格输出 JSON。

角色描述：
{character_description}

性格 / 气质：
{personality_traits}

使用场景：
{outfit_scene}

季节：
{season}

风格倾向：
{style_direction}

额外约束：
{design_constraints}

补充参考：
{reference_notes}

目标模型范式：
{target_profile}

旧模板规则摘录：
{LEGACY_OUTFIT_SYSTEM_DIGEST or "无"}

服装词典候选（仅在合适时使用，不要机械全抄）：
{candidate_block}

服装结构候选（必须先选一种主结构，再填充单品，不要默认永远内外搭）：
{chr(10).join(f"- {item}" for item in outfit_structures)}

请输出一个 JSON 对象，字段必须包含：
{{
  "outfit_direction_cn": "string",
  "selected_structure_cn": "string",
  "style_tags_en": ["string", "string"],
  "detail_tags_en": ["string", "string"],
  "normalized_tags_en": ["string", "string"],
  "expanded_tags_en": ["string", "string"],
  "quality_tags_en": ["string", "string"],
  "negative_tags_en": ["string", "string"],
  "selected_clothing_notes_cn": ["string", "string"],
  "notes_cn": "string"
}}

要求：
- 输出的是二次元 Danbooru 服装 tag，不是写实服装段落
- 优先保持少女感、可读性和立绘适配性
- 尽量利用服装词典候选，但要根据角色气质做筛选和组合
- 不要把服装做成单一固定模板，不要机械复制旧示例
- 必须先决定本次采用哪一种服装结构；不要默认总是“内搭+外套”双层结构
- selected_structure_cn 必须明确写出这次选择的主结构
- 必须再选 1 到 3 个 subtle 细节点缀，写入 detail_tags_en；这些点缀应该来自领口、袖口、腰部、小饰品、发饰、鞋部或服装状态，而不是再重复一遍主体服装
- 点缀优先追求“有记忆点但不吵闹”，例如 open_collar、sleeves_pushed_up、neck_ribbon、pendant、belt、shoe_ribbon、hair_ribbon 这类
- 如果场景、季节、角色气质不需要叠穿，优先考虑单层上衣、短袖上衣、衬衫直出、连衣裙、或 T 恤 + 下装
- 上衣款式必须跟角色气质和季节发生变化；不同角色不能总回到同一种衬衫/内搭/外套逻辑
- 下装也必须根据角色气质和场景变化；不要把 pleated_skirt 当作默认答案
- 如果角色更冷淡、利落、中性、通勤或运动，优先考虑 trousers、jeans、capris、shorts 这类裤装方案
- 如果角色更温柔、轻熟、安静，优先考虑 long_skirt、frilled_skirt 或更柔和的裙装，而不是总回到 pleated_skirt
- 常服优先追求“像这个人会穿的衣服”，不是“像这个项目里每个人都穿同一套变化版”
- 裙子和裤子不能冲突；不要出现明显不适合常规立绘的持物标签
- quality_tags_en 只放质量控制相关项
- negative_tags_en 要抑制崩坏、脏乱、跑题和不合适的成熟写实感
- 输出必须是 JSON，不要 markdown，不要解释性前言
- 你的输出第一字符必须是 {{，最后字符必须是 }}
""".strip()
    return system_prompt, user_prompt, clothing_candidates


class ManagedBackend:
    def __init__(self, config):
        self.config = config
        self.process = None
        self.process_pid = None
        self.lock = threading.RLock()
        self.prompt_profiles = load_prompt_profiles()
        self.backend_profiles = load_backend_profiles()
        self.current_backend_profile = self.config["backend"].get("default_profile")
        self.current_context_size = None
        self.current_model_name = self.config["model"]["name"]
        self.runtime_kcpps_path = None
        self.last_launch_command = []
        self.last_launch_log_path = None
        self.last_launch_error = None
        self.current_runtime_temp_subdir = None
        self.inprocess_model = None
        self.inprocess_runtime_spec = None
        self.inprocess_runtime_info = {}

    @property
    def base_url(self):
        return self.config["backend"]["base_url"].rstrip("/")

    @property
    def backend_mode(self):
        return self.config["backend"].get("mode", "managed_process")

    @property
    def backend_provider(self):
        provider = str(self.config["backend"].get("provider", "koboldcpp")).strip().lower()
        return provider or "koboldcpp"

    @property
    def is_inprocess_provider(self):
        return self.backend_provider in INPROCESS_BACKEND_PROVIDERS

    @property
    def api_root_url(self):
        raw = self.base_url
        if raw.lower().endswith("/v1"):
            return raw[:-3]
        return raw

    @property
    def openai_base_url(self):
        raw = self.base_url
        if raw.lower().endswith("/v1"):
            return raw
        return raw + "/v1"

    @property
    def health_path(self):
        configured = str(self.config["backend"].get("health_path", "") or "").strip()
        provider = self.backend_provider
        if provider in {"lm_studio", "vllm", "custom_openai_compat"}:
            return "/models"
        if provider == "llama_cpp_server":
            if not configured or configured == "/api/extra/version":
                return "/health"
            return configured
        if not configured:
            return "/api/extra/version"
        return configured

    @property
    def health_check_url(self):
        if self.backend_provider in {"lm_studio", "vllm", "custom_openai_compat"}:
            return self.openai_base_url + self.health_path
        return self.api_root_url + self.health_path

    def backend_health_is_ready(self, payload):
        if self.is_inprocess_provider:
            return self.inprocess_model is not None
        if self.backend_provider == "koboldcpp":
            if not isinstance(payload, dict):
                return False
            # KoboldCpp can expose the admin/API shell before a text model is loaded.
            return bool(payload.get("llm")) or bool(payload.get("txt2txt")) or bool(payload.get("textgen"))
        if self.backend_provider in {"lm_studio", "vllm", "custom_openai_compat", "llama_cpp_server"}:
            return isinstance(payload, dict)
        return isinstance(payload, dict)

    def backend_not_ready_reason(self, payload):
        if self.backend_provider == "koboldcpp" and isinstance(payload, dict):
            return (
                "koboldcpp responded but no text model is active "
                f"(llm={payload.get('llm')}, txt2img={payload.get('txt2img')}, "
                f"vision={payload.get('vision')}, version={payload.get('version')})"
            )
        return f"backend health payload is not ready: {payload}"

    def available_backend_profiles(self):
        return {
            key: {
                "display_name": value.get("display_name", key),
                "supports_vision": bool(value.get("supports_vision", False)),
                "default_context_size": int(value.get("default_context_size", 0) or 0),
            }
            for key, value in self.backend_profiles.items()
        }

    def resolve_backend_profile(self, backend_profile=None):
        profile_name = backend_profile or self.config["backend"].get("default_profile")
        if not profile_name:
            raise ValueError("No backend profile configured.")
        if profile_name not in self.backend_profiles:
            raise ValueError(f"Unknown backend profile: {profile_name}")
        return profile_name, self.backend_profiles[profile_name]

    def resolve_model_runtime_spec(self, backend_profile, context_size, custom_model_path=None, custom_mmproj_path=None):
        profile_name, profile = self.resolve_backend_profile(backend_profile)
        custom_model = normalize_optional_path(custom_model_path)
        custom_mmproj = normalize_optional_path(custom_mmproj_path)
        model_path = resolve_existing_path(
            custom_model or profile["model_path"],
            purpose=f"model_path for backend profile {profile_name}",
            project_subdirs=["runtime", "models"],
        )
        # A custom model path should not inherit the catalog profile's mmproj.
        # Otherwise selecting a text-only/custom Q4 model while the UI profile is
        # still a vision profile silently keeps the vision projector loaded.
        mmproj_source = custom_mmproj if custom_model else (custom_mmproj or profile.get("mmproj_path"))
        mmproj_path = resolve_existing_path(
            mmproj_source,
            purpose=f"mmproj_path for backend profile {profile_name}",
            required=False,
            project_subdirs=["runtime", "models"],
        )
        resolved_context_size = int(context_size) if context_size else int(profile.get("default_context_size", 0) or 0)
        effective_name = profile.get("model_name", profile_name)
        if custom_model:
            effective_name = Path(model_path).stem
            profile_name = f"{profile_name}__custom"
        return {
            "profile_name": profile_name,
            "profile": profile,
            "model_path": model_path,
            "mmproj_path": mmproj_path,
            "context_size": resolved_context_size,
            "effective_name": effective_name,
        }

    def build_runtime_kcpps(self, runtime_spec, runtime_options=None):
        runtime_options = normalize_runtime_options(runtime_options)
        profile_name = runtime_spec["profile_name"]
        profile = runtime_spec["profile"]
        template_path = resolve_existing_path(
            profile["template_kcpps"],
            purpose=f"template_kcpps for backend profile {profile_name}",
            project_subdirs=["config"],
        )
        runtime_config = load_json_file(template_path)
        runtime_config["model_param"] = runtime_spec["model_path"]
        runtime_config["launch"] = False
        runtime_config["showgui"] = False
        runtime_config["skiplauncher"] = True
        runtime_config["mmproj"] = runtime_spec["mmproj_path"]
        if "port" in profile:
            runtime_config["port"] = int(profile["port"])
            runtime_config["port_param"] = int(profile["port"])
        if runtime_spec["context_size"]:
            runtime_config["contextsize"] = int(runtime_spec["context_size"])
        batch_size = runtime_options.get("llama_cpp_python_n_batch")
        if batch_size is not None and str(batch_size).strip():
            runtime_config["batchsize"] = int(batch_size)
        thread_count = runtime_options.get("llama_cpp_python_threads")
        if thread_count is not None and str(thread_count).strip() and int(thread_count) > 0:
            runtime_config["threads"] = int(thread_count)

        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        context_tag = runtime_config.get("contextsize", "default")
        runtime_suffix = runtime_spec["effective_name"] if runtime_spec["effective_name"] else profile_name
        runtime_suffix = str(runtime_suffix).replace(" ", "_")
        runtime_path = RUNTIME_DIR / f"{runtime_suffix}__ctx{context_tag}.kcpps"
        with open(runtime_path, "w", encoding="utf-8") as handle:
            json.dump(runtime_config, handle, ensure_ascii=False, indent=2)
        return runtime_path

    def status(self):
        process_alive = self._is_managed_process_alive()
        healthy = bool(self.is_inprocess_provider and self.inprocess_model is not None)
        health_error = None
        health_payload = None
        if not self.is_inprocess_provider:
            try:
                health_payload = http_get_json(self.health_check_url, timeout=1)
                healthy = self.backend_health_is_ready(health_payload)
                if not healthy:
                    health_error = self.backend_not_ready_reason(health_payload)
            except Exception as error:
                health_error = str(error)

        return {
            "backend_mode": self.backend_mode,
            "backend_provider": self.backend_provider,
            "base_url": self.base_url,
            "project_dir": str(PROJECT_DIR),
            "gateway_build_id": GATEWAY_BUILD_ID,
            "process_managed_by_gateway": self.process is not None or self.process_pid is not None,
            "process_alive": process_alive,
            "backend_pid": (self.process.pid if self.process is not None and self.process.poll() is None else self.process_pid) if process_alive else None,
            "healthy": healthy,
            "health_error": health_error,
            "health_payload": health_payload,
            "model_name": self.current_model_name,
            "current_backend_profile": self.current_backend_profile,
            "current_context_size": self.current_context_size,
            "runtime_kcpps_path": self.runtime_kcpps_path,
            "runtime_temp_dir": str(RUNTIME_TEMP_DIR),
            "koboldcpp_temp_root": str(KOBOLDCPP_TEMP_ROOT),
            "current_runtime_temp_subdir": self.current_runtime_temp_subdir,
            "current_runtime_temp_path": (
                str(runtime_temp_path(self.current_runtime_temp_subdir))
                if self.current_runtime_temp_subdir
                else None
            ),
            "last_launch_command": self.last_launch_command,
            "last_launch_log_path": self.last_launch_log_path,
            "last_launch_error": self.last_launch_error,
            "last_launch_log_tail": read_text_tail(self.last_launch_log_path, max_chars=6000),
            "inprocess_loaded": self.inprocess_model is not None,
            "inprocess_runtime_info": self.inprocess_runtime_info,
            "inprocess_runtime_options": (
                self.inprocess_runtime_spec.get("runtime_options", {})
                if isinstance(self.inprocess_runtime_spec, dict)
                else {}
            ),
            "available_backend_profiles": self.available_backend_profiles(),
        }

    def _build_koboldcpp_launch_command(self, runtime_spec, runtime_options=None):
        backend = self.config["backend"]
        exe_path = resolve_koboldcpp_executable(backend.get("koboldcpp_exe"))
        runtime_path = self.build_runtime_kcpps(runtime_spec, runtime_options=runtime_options)
        self.runtime_kcpps_path = str(runtime_path)
        self.current_backend_profile = runtime_spec["profile_name"]
        self.current_context_size = runtime_spec["context_size"]
        self.current_model_name = runtime_spec["effective_name"]
        backend["koboldcpp_exe"] = exe_path
        return [exe_path, "--config", str(runtime_path)]

    def _build_llama_cpp_server_launch_command(self, runtime_spec):
        backend = self.config["backend"]
        exe_path = resolve_llama_cpp_server_executable(backend.get("llama_cpp_server_exe"))
        base_url = self.base_url
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or int(runtime_spec["profile"].get("port", 8080) or 8080)

        command = [
            exe_path,
            "--host",
            host,
            "--port",
            str(port),
            "--model",
            runtime_spec["model_path"],
        ]
        if runtime_spec["context_size"]:
            command.extend(["--ctx-size", str(int(runtime_spec["context_size"]))])
        if runtime_spec["mmproj_path"]:
            command.extend(["--mmproj", runtime_spec["mmproj_path"]])

        gpu_layers = backend.get("llama_cpp_n_gpu_layers")
        if gpu_layers is not None and str(gpu_layers).strip() != "":
            command.extend(["--n-gpu-layers", str(gpu_layers)])

        threads = backend.get("llama_cpp_threads")
        if threads is not None and str(threads).strip() != "":
            command.extend(["--threads", str(threads)])

        if bool(backend.get("llama_cpp_use_jinja", True)):
            command.append("--jinja")

        command.extend(parse_extra_launch_args(backend.get("llama_cpp_extra_args", "")))

        self.runtime_kcpps_path = None
        self.current_backend_profile = runtime_spec["profile_name"]
        self.current_context_size = runtime_spec["context_size"]
        self.current_model_name = runtime_spec["effective_name"]
        backend["llama_cpp_server_exe"] = exe_path
        return command

    def _build_launch_command(self, backend_profile, context_size, custom_model_path=None, custom_mmproj_path=None, runtime_options=None):
        runtime_spec = self.resolve_model_runtime_spec(
            backend_profile,
            context_size,
            custom_model_path,
            custom_mmproj_path,
        )
        provider = self.backend_provider
        if provider == "koboldcpp":
            return self._build_koboldcpp_launch_command(runtime_spec, runtime_options=runtime_options)
        if provider == "llama_cpp_server":
            return self._build_llama_cpp_server_launch_command(runtime_spec)
        raise ValueError(f"Unsupported managed backend provider: {provider}")

    def _resolve_llama_cpp_python_runtime_spec(self, backend_profile, context_size, custom_model_path=None, custom_mmproj_path=None):
        return self.resolve_model_runtime_spec(
            backend_profile,
            context_size,
            custom_model_path,
            custom_mmproj_path,
        )

    def _ensure_llama_cpp_python_loaded(self, backend_profile=None, context_size=None, custom_model_path=None, custom_mmproj_path=None, runtime_options=None):
        runtime_options = normalize_runtime_options(runtime_options)
        runtime_spec = self._resolve_llama_cpp_python_runtime_spec(
            backend_profile,
            context_size,
            custom_model_path=custom_model_path,
            custom_mmproj_path=custom_mmproj_path,
        )
        target_key = (
            runtime_spec["model_path"],
            int(runtime_spec["context_size"] or 0),
            runtime_spec.get("mmproj_path") or "",
            runtime_options_cache_key(runtime_options),
        )
        current_key = None
        if self.inprocess_runtime_spec:
            current_key = (
                self.inprocess_runtime_spec["model_path"],
                int(self.inprocess_runtime_spec["context_size"] or 0),
                self.inprocess_runtime_spec.get("mmproj_path") or "",
                runtime_options_cache_key(self.inprocess_runtime_spec.get("runtime_options", {})),
            )
        if self.inprocess_model is not None and current_key == target_key:
            self.current_backend_profile = runtime_spec["profile_name"]
            self.current_context_size = runtime_spec["context_size"]
            self.current_model_name = runtime_spec["effective_name"]
            return {"status": "ready", "details": self.status()}

        backend = self.config["backend"]
        n_gpu_layers = runtime_options.get(
            "llama_cpp_python_n_gpu_layers",
            backend.get("llama_cpp_python_n_gpu_layers", backend.get("llama_cpp_n_gpu_layers", -1)),
        )
        pre_cleanup_memory = get_combined_memory_snapshot()
        self._unload_inprocess_model_locked()
        pre_load_floor_mb = minimum_safe_pre_llama_load_vram_mb(
            n_gpu_layers,
            runtime_spec,
            runtime_options,
            backend,
        )
        pre_load_cleanup = free_comfy_vram_for_inprocess_llm(target_free_vram_mb=pre_load_floor_mb)
        post_cleanup_memory = get_combined_memory_snapshot()
        if pre_load_floor_mb > 0 and not bool(pre_load_cleanup.get("target_reached", False)):
            raise RuntimeError(
                "llama_cpp_python_inproc refused to load because ComfyUI did not release enough VRAM "
                f"for a safe LLM handoff: free={pre_load_cleanup.get('final_free_vram_mb')}MB, "
                f"required={pre_load_floor_mb}MB, profile={runtime_spec['profile_name']}. "
                "This usually means the image model or another CUDA allocation is still resident. "
                "Use a non-vision/text-only backend profile, lower the workflow memory load, or run the LLM "
                f"in an external subprocess/server. cleanup={pre_load_cleanup}"
            )
        Llama, runtime_info = load_llama_cpp_for_task_agent(
            prefer_private=bool(backend.get("llama_cpp_python_prefer_private", True))
        )
        n_threads = runtime_options.get(
            "llama_cpp_python_threads",
            backend.get("llama_cpp_python_threads", backend.get("llama_cpp_threads", "")),
        )
        n_batch = runtime_options.get("llama_cpp_python_n_batch", backend.get("llama_cpp_python_n_batch", 512))
        chat_format = str(
            runtime_options.get("llama_cpp_python_chat_format", backend.get("llama_cpp_python_chat_format", "")) or ""
        ).strip() or None
        verbose = bool(runtime_options.get("llama_cpp_python_verbose", backend.get("llama_cpp_python_verbose", False)))
        chat_handler = build_llama_cpp_multimodal_chat_handler(runtime_spec, runtime_options, verbose=verbose)
        n_ubatch = choose_stable_llama_cpp_ubatch(n_batch, runtime_spec["context_size"], runtime_options, backend)
        max_safe_gpu_layers = runtime_options.get(
            "llama_cpp_python_max_safe_gpu_layers",
            backend.get("llama_cpp_python_max_safe_gpu_layers", 8),
        )
        effective_gpu_layers, gpu_layer_decision = choose_safe_llama_cpp_gpu_layers(
            n_gpu_layers,
            post_cleanup_memory,
            max_gpu_layers=max_safe_gpu_layers,
        )
        init_kwargs = {
            "model_path": runtime_spec["model_path"],
            "n_ctx": int(runtime_spec["context_size"] or 4096),
            "n_batch": int(n_batch or 512),
            "n_ubatch": int(n_ubatch),
            "n_gpu_layers": int(effective_gpu_layers),
            "verbose": verbose,
        }
        if str(n_threads or "").strip():
            init_kwargs["n_threads"] = int(n_threads)
        if chat_handler is not None:
            init_kwargs["chat_handler"] = chat_handler
        elif chat_format:
            init_kwargs["chat_format"] = chat_format
        min_free_after_load_mb = minimum_safe_post_llama_load_vram_mb(runtime_spec, runtime_options, backend)

        print(
            "[TaskAgent] loading llama.cpp in-process "
            f"profile={runtime_spec['profile_name']} ctx={init_kwargs['n_ctx']} "
            f"gpu_layers={init_kwargs['n_gpu_layers']} gpu_layer_decision={gpu_layer_decision} batch={init_kwargs['n_batch']} "
            f"ubatch={init_kwargs['n_ubatch']} memory_before={pre_cleanup_memory} "
            f"pre_load_floor_mb={pre_load_floor_mb} cleanup={pre_load_cleanup} "
            f"memory_after_cleanup={post_cleanup_memory}",
            flush=True,
        )
        self.inprocess_model = Llama(**init_kwargs)
        post_load_memory = get_combined_memory_snapshot()
        post_load_cuda = post_load_memory.get("cuda", {}) if isinstance(post_load_memory, dict) else {}
        post_load_free_mb = int(post_load_cuda.get("free_mb") or 0) if isinstance(post_load_cuda, dict) else 0
        if (
            min_free_after_load_mb > 0
            and post_load_free_mb < min_free_after_load_mb
        ):
            unload_result = self._unload_inprocess_model_locked()
            raise RuntimeError(
                "llama_cpp_python_inproc stopped before generation because VRAM left after load "
                f"was unsafe: free={post_load_free_mb}MB, minimum={min_free_after_load_mb}MB, "
                f"gpu_layers={init_kwargs['n_gpu_layers']}. Lower llama_cpp_python_n_gpu_layers, "
                "use a smaller/quantized text model, or run the LLM as CPU-only/subprocess for image workflows. "
                f"unload_result={unload_result}"
            )
        print(
            "[TaskAgent] llama.cpp in-process loaded "
            f"profile={runtime_spec['profile_name']} memory_after_load={post_load_memory}",
            flush=True,
        )
        self.inprocess_runtime_spec = dict(runtime_spec)
        self.inprocess_runtime_spec["runtime_options"] = dict(runtime_options)
        self.inprocess_runtime_info = runtime_info
        self.current_backend_profile = runtime_spec["profile_name"]
        self.current_context_size = runtime_spec["context_size"]
        self.current_model_name = runtime_spec["effective_name"]
        return {
            "status": "loaded",
            "details": {
                "provider": self.backend_provider,
                "backend_profile": self.current_backend_profile,
                "context_size": self.current_context_size,
                "model_name": self.current_model_name,
                "model_path": runtime_spec["model_path"],
                "runtime_options": dict(runtime_options),
                "pre_load_cleanup": pre_load_cleanup,
                "pre_load_floor_mb": int(pre_load_floor_mb),
                "memory_before_cleanup": pre_cleanup_memory,
                "memory_after_cleanup": post_cleanup_memory,
                "memory_after_load": post_load_memory,
                "effective_n_ubatch": int(n_ubatch),
                "min_free_vram_after_load_mb": int(min_free_after_load_mb),
                "gpu_layer_decision": gpu_layer_decision,
            },
        }

    def _complete_inprocess_messages(self, payload):
        provider = self.backend_provider
        if provider == "llama_cpp_python_inproc":
            if self.inprocess_model is None:
                raise RuntimeError("llama_cpp_python_inproc runtime is not loaded.")
            raw_messages = payload.get("messages", [])
            has_image = messages_have_image_content(raw_messages)
            if has_image and not (self.inprocess_runtime_spec or {}).get("mmproj_path"):
                raise RuntimeError("llama_cpp_python_inproc received image input but no mmproj_path is loaded.")
            messages = raw_messages if has_image else normalize_messages_for_text_runtime(raw_messages)
            print(
                "[TaskAgent] llama.cpp in-process completion start "
                f"profile={self.current_backend_profile} model={self.current_model_name} "
                f"messages={len(messages)} has_image={has_image} "
                f"max_tokens={int(payload.get('max_tokens', 900))} "
                f"temperature={float(payload.get('temperature', 0.4))} "
                f"memory_before={get_combined_memory_snapshot()}",
                flush=True,
            )
            response = self.inprocess_model.create_chat_completion(
                messages=messages,
                temperature=float(payload.get("temperature", 0.4)),
                max_tokens=int(payload.get("max_tokens", 900)),
            )
            print(
                "[TaskAgent] llama.cpp in-process completion end "
                f"profile={self.current_backend_profile} memory_after={get_combined_memory_snapshot()}",
                flush=True,
            )
            return (
                response.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
        if provider == "transformers_inproc":
            raise NotImplementedError("transformers_inproc is planned but not implemented yet.")
        if provider == "clip_reuse":
            raise NotImplementedError("clip_reuse is planned but not implemented yet.")
        raise ValueError(f"Unsupported in-process provider: {provider}")

    def _complete_messages(self, payload):
        if self.is_inprocess_provider:
            return self._complete_inprocess_messages(payload)
        health_payload = http_get_json(self.health_check_url, timeout=5)
        if not self.backend_health_is_ready(health_payload):
            raise RuntimeError(self.backend_not_ready_reason(health_payload))
        response = http_post_json(self.openai_base_url + "/chat/completions", payload, timeout=900)
        return (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )

    def _unload_inprocess_model_locked(self):
        had_model = self.inprocess_model is not None
        memory_before = get_combined_memory_snapshot()
        self.inprocess_model = None
        self.inprocess_runtime_spec = None
        self.inprocess_runtime_info = {}
        self.current_context_size = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
        except Exception:
            pass
        memory_after_gc = get_combined_memory_snapshot()
        trim_result = trim_process_working_set()
        memory_after_trim = get_combined_memory_snapshot()
        result = {
            "status": "stopped" if had_model else "no_inprocess_model",
            "memory_before": memory_before,
            "memory_after_gc": memory_after_gc,
            "trim_result": trim_result,
            "memory_after_trim": memory_after_trim,
        }
        print(f"[TaskAgent] unload in-process result={result}", flush=True)
        return result

    def _is_managed_process_alive(self):
        if self.process is not None:
            return self.process.poll() is None
        if self.process_pid is None:
            return False
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {self.process_pid}", "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            output = (result.stdout or "").strip()
            return bool(output) and "No tasks are running" not in output
        try:
            os.kill(self.process_pid, 0)
            return True
        except OSError:
            return False

    def _new_launch_log_path(self):
        RUNTIME_LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        safe_provider = re.sub(r"[^a-zA-Z0-9_.-]+", "_", self.backend_provider)
        safe_profile = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(self.current_backend_profile or "unknown"))
        return RUNTIME_LOG_DIR / f"{safe_provider}_{safe_profile}_{stamp}.log"

    def _launch_failure_context(self, last_error=None):
        return {
            "backend_provider": self.backend_provider,
            "backend_profile": self.current_backend_profile,
            "context_size": self.current_context_size,
            "model_name": self.current_model_name,
            "runtime_kcpps_path": self.runtime_kcpps_path,
            "koboldcpp_temp_root": str(KOBOLDCPP_TEMP_ROOT),
            "current_runtime_temp_subdir": self.current_runtime_temp_subdir,
            "current_runtime_temp_path": (
                str(runtime_temp_path(self.current_runtime_temp_subdir))
                if self.current_runtime_temp_subdir
                else None
            ),
            "last_launch_command": self.last_launch_command,
            "last_launch_log_path": self.last_launch_log_path,
            "last_error": last_error,
            "log_tail": read_text_tail(self.last_launch_log_path, max_chars=12000),
        }

    def _start_windows_process(self, command, exe_parent, log_handle, temp_subdir):
        creationflags = 0
        for flag_name in ("CREATE_NO_WINDOW", "DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= int(getattr(subprocess, flag_name, 0) or 0)

        startupinfo = None
        if hasattr(subprocess, "STARTUPINFO") and hasattr(subprocess, "STARTF_USESHOWWINDOW"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        self.process = subprocess.Popen(
            command,
            cwd=exe_parent,
            stdout=log_handle,
            stderr=log_handle,
            stdin=subprocess.DEVNULL,
            env=runtime_temp_env(temp_subdir),
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        self.process_pid = self.process.pid

    def _start_process_locked(self, backend_profile, context_size, custom_model_path=None, custom_mmproj_path=None, runtime_options=None):
        if self.backend_mode != "managed_process":
            return {"status": "skipped", "reason": "attach_existing"}

        if self._is_managed_process_alive():
            current = self.status()
            if current.get("healthy"):
                return {"status": "already_running", "details": current}
            stale_unload = self.unload()
            print(
                "[TaskAgent] managed backend process was alive but not ready; restarted "
                f"reason={current.get('health_error')} unload={stale_unload}",
                flush=True,
            )

        command = self._build_launch_command(
            backend_profile,
            context_size,
            custom_model_path=custom_model_path,
            custom_mmproj_path=custom_mmproj_path,
            runtime_options=runtime_options,
        )
        exe_parent = str(Path(command[0]).resolve().parent)
        self.last_launch_command = [str(part) for part in command]
        self.last_launch_error = None
        parent_temp_cleanup = cleanup_runtime_temp_subdir("koboldcpp")
        launch_temp_subdir = make_runtime_temp_subdir("koboldcpp")
        self.current_runtime_temp_subdir = launch_temp_subdir
        log_path = self._new_launch_log_path()
        self.last_launch_log_path = str(log_path)
        self.process = None
        self.process_pid = None
        with open(log_path, "ab", buffering=0) as log_handle:
            header = (
                f"\n\n[TaskAgent] launching {self.backend_provider} at {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"cwd={exe_parent}\n"
                f"command={self.last_launch_command}\n"
                f"runtime_kcpps_path={self.runtime_kcpps_path}\n"
                f"backend_profile={self.current_backend_profile}\n"
                f"context_size={self.current_context_size}\n"
                f"runtime_temp_dir={runtime_temp_path(launch_temp_subdir)}\n"
                f"parent_temp_cleanup={parent_temp_cleanup}\n\n"
            )
            log_handle.write(header.encode("utf-8", errors="replace"))
            if os.name == "nt":
                self._start_windows_process(command, exe_parent, log_handle, launch_temp_subdir)
            else:
                self.process = subprocess.Popen(
                    command,
                    cwd=exe_parent,
                    stdout=log_handle,
                    stderr=log_handle,
                    env=runtime_temp_env(launch_temp_subdir),
                )
                self.process_pid = self.process.pid

        timeout_sec = int(self.config["backend"].get("startup_timeout_sec", 180))
        deadline = time.time() + timeout_sec
        grace_deadline = time.time() + 15
        last_error = None
        while time.time() < deadline:
            try:
                health_payload = http_get_json(self.health_check_url, timeout=3)
                if self.backend_health_is_ready(health_payload):
                    return {
                        "status": "started",
                        "backend_profile": self.current_backend_profile,
                        "context_size": self.current_context_size,
                        "runtime_kcpps_path": self.runtime_kcpps_path,
                        "health_payload": health_payload,
                    }
                last_error = self.backend_not_ready_reason(health_payload)
            except Exception as error:
                last_error = str(error)
                if not self._is_managed_process_alive() and time.time() >= grace_deadline:
                    return_code = self.process.returncode if self.process is not None else "unknown"
                    failure_context = self._launch_failure_context(last_error=last_error)
                    self.last_launch_error = failure_context
                    raise RuntimeError(
                        f"{self.backend_provider} failed to become healthy and launcher process is gone. "
                        f"code={return_code}, last_error={last_error}, details={failure_context}"
                    )
                time.sleep(2)

        failure_context = self._launch_failure_context(last_error=last_error)
        self.last_launch_error = failure_context
        raise TimeoutError(
            f"Timed out waiting for {self.backend_provider} to become healthy. "
            f"Last error: {last_error}, details={failure_context}"
        )

    def ensure_loaded(self, backend_profile=None, context_size=None, custom_model_path=None, custom_mmproj_path=None, runtime_options=None):
        with self.lock:
            if self.backend_provider == "llama_cpp_python_inproc":
                return self._ensure_llama_cpp_python_loaded(
                    backend_profile=backend_profile,
                    context_size=context_size,
                    custom_model_path=custom_model_path,
                    custom_mmproj_path=custom_mmproj_path,
                    runtime_options=runtime_options,
                )
            if self.backend_provider in {"transformers_inproc", "clip_reuse"}:
                raise NotImplementedError(f"{self.backend_provider} is planned but not implemented yet.")
            resolved_profile, profile = self.resolve_backend_profile(backend_profile)
            target_context = int(context_size) if context_size else int(profile.get("default_context_size", 0) or 0)
            custom_model_path = normalize_optional_path(custom_model_path)
            custom_mmproj_path = normalize_optional_path(custom_mmproj_path)
            current = self.status()
            if (
                current["healthy"]
                and current.get("current_backend_profile") == resolved_profile
                and int(current.get("current_context_size") or 0) == target_context
                and (not custom_model_path or current.get("model_name") == Path(custom_model_path).stem)
            ):
                return {"status": "ready", "details": current}
            if current["healthy"] and self._is_managed_process_alive():
                self.unload()
            return {
                "status": "loaded",
                "details": self._start_process_locked(
                    resolved_profile,
                    target_context,
                    custom_model_path=custom_model_path,
                    custom_mmproj_path=custom_mmproj_path,
                    runtime_options=runtime_options,
                ),
            }

    def unload(self):
        with self.lock:
            if self.is_inprocess_provider:
                return self._unload_inprocess_model_locked()
            stopped_pids = []
            if self.process is None and self.process_pid is None:
                port = parse_port_from_url(self.base_url, 5001)
                for pid in find_windows_pids_on_tcp_port(port):
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    stopped_pids.append(pid)
                temp_cleanup = cleanup_runtime_temp_report(self.current_runtime_temp_subdir)
                self.current_runtime_temp_subdir = None
                return {
                    "status": "no_managed_process",
                    "stopped_port_pids": stopped_pids,
                    "temp_cleanup": temp_cleanup,
                }
            if not self._is_managed_process_alive():
                self.process = None
                self.process_pid = None
                port = parse_port_from_url(self.base_url, 5001)
                for pid in find_windows_pids_on_tcp_port(port):
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    stopped_pids.append(pid)
                temp_cleanup = cleanup_runtime_temp_report(self.current_runtime_temp_subdir)
                self.current_runtime_temp_subdir = None
                return {
                    "status": "already_stopped",
                    "stopped_port_pids": stopped_pids,
                    "temp_cleanup": temp_cleanup,
                }
            pid = self.process_pid or self.process.pid
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                deadline = time.time() + 20
                while time.time() < deadline:
                    if not self._is_managed_process_alive():
                        break
                    time.sleep(0.5)
                port = parse_port_from_url(self.base_url, 5001)
                for residual_pid in find_windows_pids_on_tcp_port(port):
                    if residual_pid == pid:
                        continue
                    subprocess.run(
                        ["taskkill", "/PID", str(residual_pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    stopped_pids.append(residual_pid)
            else:
                self.process.terminate()
                try:
                    self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)

            self.process = None
            self.process_pid = None
            temp_cleanup = cleanup_runtime_temp_report(self.current_runtime_temp_subdir)
            self.current_runtime_temp_subdir = None

            deadline = time.time() + 15
            last_error = None
            while time.time() < deadline:
                try:
                    http_get_json(self.health_check_url, timeout=2)
                    time.sleep(0.5)
                except Exception as error:
                    last_error = str(error)
                    return {
                        "status": "stopped",
                        "health_after_unload": "offline",
                        "health_error": last_error,
                        "stopped_port_pids": stopped_pids,
                        "temp_cleanup": temp_cleanup,
                    }

            return {
                "status": "stopped",
                "health_after_unload": "still_responding",
                "health_error": last_error,
                "stopped_port_pids": stopped_pids,
                "temp_cleanup": temp_cleanup,
            }

    def run_task(
        self,
        task_type,
        inputs,
        temperature,
        max_tokens,
        auto_load_backend,
        unload_after_run,
        backend_profile=None,
        context_size=None,
        custom_model_path=None,
        custom_mmproj_path=None,
        runtime_options=None,
    ):
        if auto_load_backend:
            self.ensure_loaded(
                backend_profile=backend_profile,
                context_size=context_size,
                custom_model_path=custom_model_path,
                custom_mmproj_path=custom_mmproj_path,
                runtime_options=runtime_options,
            )

        task_type, inputs = resolve_task_request(task_type, inputs)
        inputs = preprocess_task_inputs(task_type, inputs)

        if task_type == "generate_character_design":
            system_prompt, user_prompt = build_character_design_prompts(inputs)
        elif task_type == "expand_anime_tags":
            system_prompt, user_prompt = build_expand_tags_prompts(inputs)
        elif task_type == "translate_anime_tags":
            system_prompt, user_prompt = build_translate_tags_prompts(inputs)
        elif task_type == "normalize_anime_tags":
            system_prompt, user_prompt = build_normalize_tags_prompts(inputs)
        elif task_type in ("extract_tags_from_image", "vision_tagging", "image_captioning"):
            system_prompt, user_prompt = build_visual_tagging_prompts(inputs)
        elif task_type == "refine_wd14_tags":
            system_prompt, user_prompt = build_refine_wd14_prompts(inputs)
        elif task_type == "generate_natural_caption":
            system_prompt, user_prompt = build_natural_caption_prompts(inputs)
        elif task_type == "generate_outfit_tags":
            system_prompt, user_prompt, clothing_candidates = build_outfit_generation_prompts(inputs)
            inputs["retrieved_clothing_candidates"] = clothing_candidates
        else:
            raise ValueError(f"Unsupported task_type: {task_type}")

        system_prompt, user_prompt = apply_runtime_context(system_prompt, user_prompt, inputs)
        user_message_content = build_user_message_content(task_type, user_prompt, inputs)

        payload = {
            "model": self.current_model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message_content},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        raw_text = self._complete_messages(payload)
        parse_error = None
        try:
            json_result = extract_first_json_object(raw_text)
            status = "success"
        except Exception as error:
            parse_error = str(error)
            json_result = fallback_json_result(task_type, raw_text, inputs, parse_error)
            status = "parse_fallback"
        json_result = enrich_json_result(task_type, json_result, inputs)
        if task_type == "generate_outfit_tags":
            json_result["retrieved_clothing_candidates"] = [
                {
                    "score": item["score"],
                    "tag": item["record"].get("tag", ""),
                    "name_zh": item["record"].get("name_zh", ""),
                    "group_zh": item["record"].get("group_zh", ""),
                    "definition": item["record"].get("definition", ""),
                }
                for item in inputs.get("retrieved_clothing_candidates", [])[:18]
            ]
            expand_inputs = build_outfit_post_expand_inputs(inputs, json_result)
            expand_system_prompt, expand_user_prompt = build_expand_tags_prompts(expand_inputs)
            expand_payload = {
                "model": self.current_model_name,
                "messages": [
                    {"role": "system", "content": expand_system_prompt},
                    {"role": "user", "content": expand_user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            expand_raw_text = self._complete_messages(expand_payload)
            try:
                expand_json = extract_first_json_object(expand_raw_text)
                expand_json = enrich_json_result("expand_anime_tags", expand_json, expand_inputs)
                json_result = merge_outfit_expansion_result(json_result, expand_json, inputs)
                json_result["post_expand_status"] = "success"
                json_result["post_expand_raw_text"] = expand_raw_text
            except Exception as expand_error:
                expand_fallback = fallback_json_result("expand_anime_tags", expand_raw_text, expand_inputs, str(expand_error))
                expand_fallback = enrich_json_result("expand_anime_tags", expand_fallback, expand_inputs)
                json_result = merge_outfit_expansion_result(json_result, expand_fallback, inputs)
                json_result["post_expand_status"] = "parse_fallback"
                json_result["post_expand_error"] = str(expand_error)
                json_result["post_expand_raw_text"] = expand_raw_text
            json_result.update(profile_format_prompt(inputs.get("target_profile", "generic_tag_model"), json_result))

        result = {
            "status": status,
            "task_type": task_type,
            "backend_mode": self.backend_mode,
            "base_url": self.base_url,
            "model_name": self.current_model_name,
            "backend_profile": self.current_backend_profile,
            "context_size": self.current_context_size,
            "raw_text": raw_text,
            "json_result": json_result,
            "resolved_request": {
                "task_type": task_type,
                "target_profile": str(inputs.get("target_profile", "")).strip(),
                "direction": str(inputs.get("direction", "")).strip(),
                "style_hint": str(inputs.get("style_hint", "")).strip(),
                "purpose": str(inputs.get("purpose", "")).strip(),
                "input_keys": sorted(list(inputs.keys())),
            },
        }
        if parse_error is not None:
            result["parse_error"] = parse_error

        effective_unload = bool(unload_after_run)
        if not effective_unload:
            effective_unload = bool(self.config["backend"].get("default_unload_after_run", False))
        if effective_unload:
            result["unload_result"] = self.unload()

        return result


class GatewayHandler(BaseHTTPRequestHandler):
    backend = None

    def _send_json(self, status_code, payload):
        body = dump_json(payload)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status_code, body):
        data = body.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html = FRONTEND_PATH.read_text(encoding="utf-8")
            self._send_html(HTTPStatus.OK, html)
            return
        if self.path == "/backend/status":
            self._send_json(HTTPStatus.OK, self.backend.status())
            return
        if self.path == "/backend/profiles":
            self._send_json(HTTPStatus.OK, self.backend.available_backend_profiles())
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return

        try:
            if self.path == "/backend/load":
                result = self.backend.ensure_loaded(
                    backend_profile=payload.get("backend_profile"),
                    context_size=payload.get("context_size"),
                    custom_model_path=payload.get("custom_model_path"),
                    custom_mmproj_path=payload.get("custom_mmproj_path"),
                    runtime_options=payload.get("runtime_options"),
                )
                self._send_json(HTTPStatus.OK, result)
                return
            if self.path == "/backend/unload":
                result = self.backend.unload()
                self._send_json(HTTPStatus.OK, result)
                return
            if self.path == "/run_task":
                result = self.backend.run_task(
                    task_type=payload.get("task_type", ""),
                    inputs=payload.get("inputs", {}),
                    temperature=float(payload.get("temperature", self.backend.config["task_defaults"]["temperature"])),
                    max_tokens=int(payload.get("max_tokens", self.backend.config["task_defaults"]["max_tokens"])),
                    auto_load_backend=bool(payload.get("auto_load_backend", True)),
                    unload_after_run=bool(payload.get("unload_after_run", False)),
                    backend_profile=payload.get("backend_profile"),
                    context_size=payload.get("context_size"),
                    custom_model_path=payload.get("custom_model_path"),
                    custom_mmproj_path=payload.get("custom_mmproj_path"),
                    runtime_options=payload.get("runtime_options"),
                )
                self._send_json(HTTPStatus.OK, result)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except urllib.error.HTTPError as error:
            try:
                body = error.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                {
                    "error": "upstream_http_error",
                    "status_code": error.code,
                    "body": body,
                },
            )
        except Exception as error:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "gateway_error", "detail": str(error)},
            )


def main():
    parser = argparse.ArgumentParser(description="Gemma4 E4B task gateway baseline")
    parser.add_argument(
        "--config",
        default=str(PROJECT_DIR / "config" / "task_agent_config.local.json"),
        help="Path to gateway config JSON",
    )
    args = parser.parse_args()

    config = load_json_file(args.config)
    backend = ManagedBackend(config)
    GatewayHandler.backend = backend

    host = config["gateway"]["host"]
    port = int(config["gateway"]["port"])
    server = ThreadingHTTPServer((host, port), GatewayHandler)
    print(f"[task-gateway] listening on http://{host}:{port}")
    print(f"[task-gateway] backend base_url={backend.base_url}")
    print(f"[task-gateway] backend_mode={backend.backend_mode}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            backend.unload()
        except Exception:
            pass
        server.server_close()


if __name__ == "__main__":
    main()
