import json
import importlib.util
import os
import tempfile
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse


NODE_DIR = Path(__file__).resolve().parent


def is_task_agent_bundle_root(path):
    candidate = Path(path)
    return (
        candidate.exists()
        and (candidate / "task_agent_gateway.py").exists()
        and (candidate / "config").exists()
    )


def resolve_project_dir():
    candidates = []

    env_override = str(os.getenv("TASK_AGENT_BUNDLE_ROOT", "")).strip()
    if env_override:
        candidates.append(Path(env_override))

    for override_name in ("task_agent_bundle_root.txt", "task_agent_bundle_root.local.txt"):
        override_path = NODE_DIR / override_name
        if override_path.exists():
            try:
                override_text = override_path.read_text(encoding="utf-8").strip()
            except UnicodeDecodeError:
                override_text = override_path.read_text(encoding="utf-8", errors="replace").strip()
            if override_text:
                candidates.append(Path(override_text))

    candidates.extend(
        [
            NODE_DIR.parent,
            NODE_DIR,
        ]
    )

    for candidate in candidates:
        if is_task_agent_bundle_root(candidate):
            return candidate

    for ancestor in NODE_DIR.parents:
        if is_task_agent_bundle_root(ancestor):
            return ancestor

    return NODE_DIR.parent


PROJECT_DIR = resolve_project_dir()
BACKEND_PROFILES_PATH = PROJECT_DIR / "config" / "backend_profiles.json"
GATEWAY_CONFIG_PATH = PROJECT_DIR / "config" / "task_agent_config.local.json"
GATEWAY_SCRIPT_PATH = PROJECT_DIR / "task_agent_gateway.py"
LOCAL_GATEWAY_BUILD_ID = str(GATEWAY_SCRIPT_PATH.stat().st_mtime_ns) if GATEWAY_SCRIPT_PATH.exists() else ""
_GATEWAY_PROCESS = None
_GATEWAY_PROCESS_PID = None
_DIRECT_GATEWAY_MODULE = None
_DIRECT_GATEWAY_MODULE_KEY = None
_DIRECT_BACKENDS = {}
_DIRECT_BACKENDS_LOCK = threading.Lock()


def load_backend_profiles():
    if BACKEND_PROFILES_PATH.exists():
        return json.loads(BACKEND_PROFILES_PATH.read_text(encoding="utf-8"))
    return {}


LEGACY_BACKEND_PROFILE_OPTIONS = {
    "gemma4_e4b_q4": [
        "gemma4_e4b_q4 | Gemma 4 E4B Q4",
    ],
    "gemma4_26b_q4": [
        "gemma4_26b_q4 | Gemma 4 26B A4B Q4",
    ],
}


def get_backend_profile_options():
    profiles = load_backend_profiles()
    options = []
    for key, spec in profiles.items():
        display_name = spec.get("display_name", key)
        if spec.get("supports_vision"):
            display_name += " [Vision]"
        options.append(f"{key} | {display_name}")
        for legacy_option in LEGACY_BACKEND_PROFILE_OPTIONS.get(key, []):
            if legacy_option not in options:
                options.append(legacy_option)
    if not options:
        options.append("gemma4_e4b_q4 | Gemma 4 E4B Q4")
    return options


BACKEND_PROFILE_OPTIONS = get_backend_profile_options()
DEFAULT_BACKEND_PROFILE = BACKEND_PROFILE_OPTIONS[0]
BACKEND_PROVIDER_OPTIONS = [
    "config_default",
    "llama_cpp_python_inproc",
    "transformers_inproc",
    "clip_reuse",
    "koboldcpp",
    "llama_cpp_server",
    "lm_studio",
    "vllm",
    "custom_openai_compat",
]
MANAGED_BACKEND_PROVIDERS = {"koboldcpp", "llama_cpp_server"}
ATTACH_BACKEND_PROVIDERS = {"lm_studio", "vllm", "custom_openai_compat"}
INPROCESS_BACKEND_PROVIDERS = {"llama_cpp_python_inproc", "transformers_inproc", "clip_reuse"}
DEFAULT_ATTACH_BASE_URLS = {
    "lm_studio": "http://127.0.0.1:1234/v1",
    "vllm": "http://127.0.0.1:8000/v1",
}


def parse_backend_profile_choice(choice):
    if " | " in str(choice):
        return str(choice).split(" | ", 1)[0].strip()
    return str(choice).strip()


def normalize_backend_provider_choice(choice):
    value = str(choice or "").strip().lower()
    if not value:
        return "config_default"
    return value


def default_base_url_for_provider(provider_choice):
    return DEFAULT_ATTACH_BASE_URLS.get(normalize_backend_provider_choice(provider_choice), "")


def post_json(url, payload, timeout=600):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def load_json_file(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_direct_gateway_module():
    global _DIRECT_GATEWAY_MODULE, _DIRECT_GATEWAY_MODULE_KEY
    module_path = GATEWAY_SCRIPT_PATH
    module_key = str(module_path.resolve()) if module_path.exists() else str(module_path)
    if _DIRECT_GATEWAY_MODULE is not None and _DIRECT_GATEWAY_MODULE_KEY == module_key:
        return _DIRECT_GATEWAY_MODULE
    if not module_path.exists():
        raise RuntimeError(f"Missing task_agent_gateway.py at {module_path}")
    spec = importlib.util.spec_from_file_location("task_agent_gateway_direct_runtime", str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load task_agent_gateway module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _DIRECT_GATEWAY_MODULE = module
    _DIRECT_GATEWAY_MODULE_KEY = module_key
    return module


def direct_backend_cache_key(config_path, backend_base_url, backend_provider):
    return (
        str(Path(config_path).resolve()),
        str(backend_base_url or "").strip(),
        normalize_backend_provider_choice(backend_provider),
    )


def get_direct_backend(config_path=None, backend_base_url=None, backend_provider=None):
    config_path = str(config_path or GATEWAY_CONFIG_PATH)
    cache_key = direct_backend_cache_key(config_path, backend_base_url, backend_provider)
    with _DIRECT_BACKENDS_LOCK:
        backend = _DIRECT_BACKENDS.get(cache_key)
        if backend is not None:
            return backend

        module = load_direct_gateway_module()
        config = load_json_file(config_path)
        provider_choice = normalize_backend_provider_choice(backend_provider)
        backend_config = dict(config.get("backend", {}) or {})
        if provider_choice != "config_default":
            backend_config["provider"] = provider_choice
            if provider_choice in ATTACH_BACKEND_PROVIDERS:
                backend_config["mode"] = "attach_existing"
            elif provider_choice in MANAGED_BACKEND_PROVIDERS:
                backend_config["mode"] = "managed_process"
            elif provider_choice in INPROCESS_BACKEND_PROVIDERS:
                backend_config["mode"] = "inprocess"
            default_attach_base = default_base_url_for_provider(provider_choice)
            if (
                default_attach_base
                and not str(backend_config.get("base_url", "")).strip()
                and not str(backend_base_url or "").strip()
            ):
                backend_config["base_url"] = default_attach_base
        if str(backend_base_url or "").strip():
            config = dict(config)
            backend_config["base_url"] = str(backend_base_url).strip()
            if (
                provider_choice == "config_default"
                and backend_config.get("mode") == "managed_process"
            ):
                backend_config["mode"] = "attach_existing"
        config = dict(config)
        config["backend"] = backend_config
        backend = module.ManagedBackend(config)
        _DIRECT_BACKENDS[cache_key] = backend
        return backend


def maybe_backend_base_url_from_gateway_field(gateway_url):
    value = str(gateway_url or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered.endswith(":8765") or lowered.endswith(":8765/"):
        return ""
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return value
    return ""


def resolve_backend_base_url(gateway_url, backend_provider):
    explicit = maybe_backend_base_url_from_gateway_field(gateway_url)
    if explicit:
        return explicit
    return default_base_url_for_provider(backend_provider)


def run_task_direct(
    *,
    gateway_url,
    backend_provider,
    task_type,
    inputs,
    temperature,
    max_tokens,
    auto_load_backend,
    unload_after_run,
    backend_profile,
    context_size,
    custom_model_path,
    custom_mmproj_path,
    runtime_options=None,
):
    backend_base_url = resolve_backend_base_url(gateway_url, backend_provider)
    backend = get_direct_backend(
        config_path=GATEWAY_CONFIG_PATH,
        backend_base_url=backend_base_url,
        backend_provider=backend_provider,
    )
    return backend.run_task(
        task_type=task_type,
        inputs=inputs,
        temperature=temperature,
        max_tokens=max_tokens,
        auto_load_backend=auto_load_backend,
        unload_after_run=unload_after_run,
        backend_profile=backend_profile,
        context_size=context_size,
        custom_model_path=custom_model_path,
        custom_mmproj_path=custom_mmproj_path,
        runtime_options=runtime_options or {},
    )


def gateway_status_url(gateway_url):
    return gateway_url.rstrip("/") + "/backend/status"


def fetch_gateway_status(gateway_url, timeout=6):
    request = urllib.request.Request(gateway_status_url(gateway_url), method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def gateway_is_healthy(gateway_url, timeout=6):
    payload = fetch_gateway_status(gateway_url, timeout=timeout)
    return bool(payload.get("backend_mode"))


def is_local_gateway_url(gateway_url):
    value = str(gateway_url or "").strip().lower()
    return value.startswith("http://127.0.0.1:") or value.startswith("http://localhost:")


def parse_gateway_host_port(gateway_url):
    parsed = urlparse(str(gateway_url or "").strip())
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8765
    return host, port


def can_bind_port(host, port):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def gateway_matches_local_bundle(payload):
    project_dir = str(payload.get("project_dir", "")).strip().lower()
    expected_dir = str(PROJECT_DIR).strip().lower()
    build_id = str(payload.get("gateway_build_id", "")).strip()
    if project_dir and project_dir != expected_dir:
        return False
    if build_id and LOCAL_GATEWAY_BUILD_ID and build_id != LOCAL_GATEWAY_BUILD_ID:
        return False
    if not project_dir or not build_id:
        return False
    return True


def gateway_status_is_usable(payload):
    if not isinstance(payload, dict):
        return False
    return bool(
        payload.get("backend_mode")
        or payload.get("project_dir")
        or payload.get("base_url")
    )


def find_listening_pids_on_port(port):
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    pids = set()
    needle = f":{int(port)}"
    for line in (result.stdout or "").splitlines():
        text = line.strip()
        if not text or "LISTENING" not in text.upper():
            continue
        if needle not in text:
            continue
        parts = text.split()
        if len(parts) >= 5 and parts[-1].isdigit():
            pids.add(int(parts[-1]))
    return sorted(pids)


def kill_processes_on_port(port):
    killed = []
    for pid in find_listening_pids_on_port(port):
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        killed.append(pid)
    return killed


def start_local_gateway(gateway_url):
    global _GATEWAY_PROCESS, _GATEWAY_PROCESS_PID

    if not GATEWAY_SCRIPT_PATH.exists() or not GATEWAY_CONFIG_PATH.exists():
        raise RuntimeError("Local gateway script or config is missing.")

    if _GATEWAY_PROCESS is not None and _GATEWAY_PROCESS.poll() is None:
        return
    if _GATEWAY_PROCESS_PID is not None and os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {_GATEWAY_PROCESS_PID}", "/FO", "CSV", "/NH"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        output = (result.stdout or "").strip()
        if output and "No tasks are running" not in output:
            return

    if sys.platform.startswith("win"):
        py_escaped = str(sys.executable).replace("'", "''")
        script_escaped = str(GATEWAY_SCRIPT_PATH).replace("'", "''")
        config_escaped = str(GATEWAY_CONFIG_PATH).replace("'", "''")
        cwd_escaped = str(PROJECT_DIR).replace("'", "''")
        ps_script = (
            f"$p = Start-Process -FilePath '{py_escaped}' "
            f"-ArgumentList @('{script_escaped}','--config','{config_escaped}') "
            f"-WorkingDirectory '{cwd_escaped}' "
            "-WindowStyle Hidden -PassThru; "
            "Write-Output $p.Id"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(PROJECT_DIR),
            check=False,
        )
        pid_text = (result.stdout or "").strip()
        if result.returncode != 0 or not pid_text:
            raise RuntimeError(
                f"failed_to_start_local_gateway_via_powershell: returncode={result.returncode}, "
                f"stdout={result.stdout!r}, stderr={result.stderr!r}"
            )
        _GATEWAY_PROCESS = None
        _GATEWAY_PROCESS_PID = int(pid_text.splitlines()[-1].strip())
        return

    command = [sys.executable, str(GATEWAY_SCRIPT_PATH), "--config", str(GATEWAY_CONFIG_PATH)]
    _GATEWAY_PROCESS = subprocess.Popen(
        command,
        cwd=str(PROJECT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )


def ensure_gateway_available(gateway_url, auto_start=True, timeout_sec=30):
    if not auto_start or not is_local_gateway_url(gateway_url):
        return

    try:
        status_payload = fetch_gateway_status(gateway_url, timeout=6)
        if gateway_status_is_usable(status_payload) and gateway_matches_local_bundle(status_payload):
            return
        if gateway_status_is_usable(status_payload):
            host, port = parse_gateway_host_port(gateway_url)
            kill_processes_on_port(port)
            time.sleep(1.0)
    except Exception:
        pass

    host, port = parse_gateway_host_port(gateway_url)
    if not can_bind_port(host, port):
        deadline = time.time() + timeout_sec
        last_error = None
        while time.time() < deadline:
            try:
                status_payload = fetch_gateway_status(gateway_url, timeout=6)
                if gateway_status_is_usable(status_payload):
                    return
            except Exception as error:
                last_error = str(error)
                time.sleep(0.5)
        raise RuntimeError(f"Gateway port is occupied but the local task gateway is not responding. {last_error or ''}".strip())

    start_local_gateway(gateway_url)
    time.sleep(0.8)

    deadline = time.time() + timeout_sec
    last_error = None
    while time.time() < deadline:
        try:
            status_payload = fetch_gateway_status(gateway_url, timeout=6)
            if gateway_status_is_usable(status_payload):
                return
        except Exception as error:
            last_error = str(error)
        time.sleep(0.5)
    raise RuntimeError(f"Failed to auto-start local gateway: {last_error or 'unknown error'}")


def build_common_inputs():
    return {
        "gateway_url": (
            "STRING",
            {"default": "http://127.0.0.1:8765", "multiline": False},
        ),
        "backend_provider": (
            BACKEND_PROVIDER_OPTIONS,
        ),
        "model_source": (
            ["profile_catalog", "custom_path"],
        ),
        "backend_profile": (
            BACKEND_PROFILE_OPTIONS,
        ),
        "custom_model_path": (
            "STRING",
            {"default": "", "multiline": False},
        ),
        "custom_mmproj_path": (
            "STRING",
            {"default": "", "multiline": False},
        ),
        "context_size": ("INT", {"default": 16384, "min": 2048, "max": 65536, "step": 1024}),
        "llama_cpp_python_n_gpu_layers": ("INT", {"default": 0, "min": -1, "max": 999, "step": 1}),
        "llama_cpp_python_n_batch": ("INT", {"default": 512, "min": 32, "max": 4096, "step": 32}),
        "llama_cpp_python_threads": ("INT", {"default": 0, "min": 0, "max": 128, "step": 1}),
        "auto_load_backend": ("BOOLEAN", {"default": True}),
        "unload_after_run": ("BOOLEAN", {"default": False}),
    }


def build_runtime_options(llama_cpp_python_n_gpu_layers, llama_cpp_python_n_batch, llama_cpp_python_threads):
    options = {
        "llama_cpp_python_n_gpu_layers": int(llama_cpp_python_n_gpu_layers),
        "llama_cpp_python_n_batch": int(llama_cpp_python_n_batch),
    }
    if int(llama_cpp_python_threads) > 0:
        options["llama_cpp_python_threads"] = int(llama_cpp_python_threads)
    return options


def backend_choice_supports_vision(backend_profile, custom_mmproj_path):
    if str(custom_mmproj_path or "").strip():
        return True
    profile_key = parse_backend_profile_choice(backend_profile).lower()
    return "vision" in profile_key


def inject_backend_payload(payload, backend_provider, model_source, backend_profile, custom_model_path, custom_mmproj_path):
    resolved_backend_profile = parse_backend_profile_choice(backend_profile)
    payload["backend_provider"] = normalize_backend_provider_choice(backend_provider)
    payload["backend_profile"] = resolved_backend_profile
    payload["model_source"] = model_source
    if model_source == "custom_path" and str(custom_model_path).strip():
        payload["custom_model_path"] = str(custom_model_path).strip()
    if str(custom_mmproj_path).strip():
        payload["custom_mmproj_path"] = str(custom_mmproj_path).strip()
    return payload


def custom_model_looks_like_vision_family(custom_model_path):
    path_text = str(custom_model_path or "").strip().lower()
    return "gemma" in path_text and ("hauhaucs" in path_text or "vision" in path_text or "aggressive" in path_text)


def request_needs_mmproj(model_source, custom_model_path, custom_mmproj_path, resolved_task_type, inputs):
    if model_source != "custom_path":
        return False
    if str(custom_mmproj_path or "").strip():
        return False
    if not custom_model_looks_like_vision_family(custom_model_path):
        return False

    image_path = str(inputs.get("image_path", "")).strip()
    image_tasks = {"extract_tags_from_image", "vision_tagging", "image_captioning"}
    return bool(image_path) or str(resolved_task_type or "").strip() in image_tasks


def build_context_optional_inputs():
    return {
        "context_bundle_json": (
            "STRING",
            {"forceInput": True},
        ),
    }


def build_legacy_context_optional_inputs():
    return {
        "system_prompt_override": (
            "STRING",
            {"forceInput": True},
        ),
        "character_card_text": (
            "STRING",
            {"forceInput": True},
        ),
        "world_book_text": (
            "STRING",
            {"forceInput": True},
        ),
        "regex_rules_text": (
            "STRING",
            {"forceInput": True},
        ),
        "context_bundle_json": (
            "STRING",
            {"forceInput": True},
        ),
        "image_path": (
            "STRING",
            {"forceInput": True},
        ),
    }


def read_text_file_if_exists(file_path):
    path_text = str(file_path or "").strip()
    if not path_text:
        return "", ""
    path = Path(path_text)
    if not path.exists():
        return "", f"missing_file: {path}"
    try:
        return path.read_text(encoding="utf-8"), ""
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace"), ""


def resolve_file_or_text(direct_text, file_path):
    file_text, file_error = read_text_file_if_exists(file_path)
    if file_text.strip():
        return file_text.strip(), file_error
    return str(direct_text or "").strip(), file_error


def parse_json_dict_or_none(text):
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def parse_json_value_or_none(text):
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def compose_context_bundle_internal(
    *,
    task_type_override="",
    system_prompt_text="",
    character_card_text="",
    world_book_text="",
    regex_rules_text="",
    extra_notes_text="",
    task_config_text="",
    system_prompt_path="",
    character_card_path="",
    world_book_path="",
    regex_rules_path="",
    task_config_path="",
    task_bundle_path="",
    resource_bundle_path="",
    context_bundle_path="",
    image_path="",
    task_bundle_json="",
    resource_bundle_json="",
):
    system_prompt_final, system_prompt_error = resolve_file_or_text(system_prompt_text, system_prompt_path)
    character_card_final, character_card_error = resolve_file_or_text(character_card_text, character_card_path)
    world_book_final, world_book_error = resolve_file_or_text(world_book_text, world_book_path)
    regex_rules_final, regex_rules_error = resolve_file_or_text(regex_rules_text, regex_rules_path)
    task_config_final, task_config_error = resolve_file_or_text(task_config_text, task_config_path)
    task_bundle_final, task_bundle_error = resolve_file_or_text(task_bundle_json, task_bundle_path)
    resource_bundle_final, resource_bundle_error = resolve_file_or_text(resource_bundle_json, resource_bundle_path)
    context_bundle_file_text, context_bundle_error = read_text_file_if_exists(context_bundle_path)

    base_bundle = parse_json_dict_or_none(context_bundle_file_text) or {}
    bundle = dict(base_bundle)
    bundle.update({
        "system_prompt_override": system_prompt_final,
        "character_card_text": character_card_final,
        "world_book_text": world_book_final,
        "regex_rules_text": regex_rules_final,
        "extra_notes_text": str(extra_notes_text).strip(),
        "image_path": str(image_path).strip(),
    })

    task_config_bundle = parse_json_dict_or_none(task_config_final)
    if task_config_bundle:
        bundle["task_config"] = task_config_bundle
    elif str(task_config_final).strip():
        bundle["task_config_text"] = str(task_config_final).strip()

    task_bundle_value = parse_json_value_or_none(task_bundle_final)
    if isinstance(task_bundle_value, dict):
        bundle["task_bundle"] = task_bundle_value
    elif str(task_bundle_final).strip():
        bundle["task_bundle_text"] = str(task_bundle_final).strip()

    resource_bundle_value = parse_json_value_or_none(resource_bundle_final)
    if isinstance(resource_bundle_value, list):
        for item in resource_bundle_value:
            bundle = apply_resource_bundle_to_context(bundle, item)
    elif isinstance(resource_bundle_value, dict):
        bundle = apply_resource_bundle_to_context(bundle, resource_bundle_value)
    elif str(resource_bundle_final).strip():
        bundle["resource_bundle_text"] = str(resource_bundle_final).strip()

    if str(task_type_override).strip():
        bundle["task_type"] = str(task_type_override).strip()
    bundle = {key: value for key, value in bundle.items() if value}
    if context_bundle_file_text.strip() and not parse_json_dict_or_none(context_bundle_file_text):
        bundle["file_bundle_text"] = context_bundle_file_text.strip()

    errors = [
        item
        for item in [
            system_prompt_error,
            character_card_error,
            world_book_error,
            regex_rules_error,
            task_config_error,
            task_bundle_error,
            resource_bundle_error,
            context_bundle_error,
        ]
        if item
    ]
    return (
        json.dumps(bundle, ensure_ascii=False, indent=2),
        "ok" if not errors else "; ".join(errors),
    )


def get_builtin_resource_catalog():
    resources_dir = PROJECT_DIR / "resources"
    task_templates_dir = resources_dir / "task_templates"
    task_bundles_dir = resources_dir / "task_bundles"
    catalog = {}

    if task_templates_dir.exists():
        for path in sorted(task_templates_dir.glob("*.json")):
            catalog[f"task_template::{path.stem}"] = {
                "path": str(path),
                "resource_role": "task_template",
                "parse_mode": "json",
                "description": f"任务模板: {path.stem}",
            }

    if task_bundles_dir.exists():
        for path in sorted(task_bundles_dir.glob("*.json")):
            catalog[f"task_bundle::{path.stem}"] = {
                "path": str(path),
                "resource_role": "task_bundle",
                "parse_mode": "json",
                "description": f"任务链预设: {path.stem}",
            }

    builtin_specs = {
        "resource::danbooru_character_aliases": (
            resources_dir / "danbooru_character_aliases.json",
            "danbooru_character_dict",
            "path",
            "Danbooru 角色手工别名字典",
        ),
        "resource::danbooru_character_aliases_generated": (
            resources_dir / "danbooru_character_aliases.generated.json",
            "danbooru_character_dict",
            "path",
            "Danbooru 角色自动别名字典",
        ),
        "resource::character_alias_safety": (
            resources_dir / "character_alias_safety.json",
            "danbooru_character_safety",
            "path",
            "角色 alias 安全过滤规则",
        ),
        "resource::danbooru_clothing_jsonl": (
            resources_dir / "Danbooru服装查询资源_本地版_2026-05-26.jsonl",
            "danbooru_clothing_dict",
            "path",
            "Danbooru 服装结构化词典",
        ),
        "resource::danbooru_clothing_hierarchy_txt": (
            resources_dir / "Danbooru服装查询资源_层级版_2026-05-26.txt",
            "reference_notes",
            "text",
            "Danbooru 服装层级知识文本",
        ),
        "resource::danbooru_tag_cooccurrence_csv": (
            resources_dir / "danbooru_tags_cooccurrence.csv",
            "danbooru_cooccurrence",
            "path",
            "Danbooru tag 共现统计 CSV",
        ),
        "resource::legacy_outfit_system": (
            resources_dir / "服装生成模板_system_legacy.md",
            "system_prompt",
            "text",
            "旧服装生成规则模板",
        ),
        "resource::danbooru_artist_wildcard_text": (
            resources_dir / "danbooru_artist_wildcard-D站画师列表.txt",
            "reference_notes",
            "text",
            "Danbooru 画师 wildcard 文本资源，可供 LLM 参考画师 tag 和画风关联",
        ),
        "resource::danbooru_character_webui_normalized": (
            resources_dir / "danbooru_character_webui.normalized.jsonl",
            "danbooru_character_corpus",
            "path",
            "Danbooru 角色结构化词典 JSONL，可供角色检索和规范 tag 参考",
        ),
        "resource::danbooru_tag_count_stats": (
            resources_dir / "tag_count_tags_统计.jsonl",
            "danbooru_tag_stats",
            "path",
            "Danbooru tag 频次统计 JSONL，可供 tag 热度和过滤参考",
        ),
    }
    for key, (path, role, parse_mode, description) in builtin_specs.items():
        if path.exists():
            catalog[key] = {
                "path": str(path),
                "resource_role": role,
                "parse_mode": parse_mode,
                "description": description,
            }
    return catalog


BUILTIN_RESOURCE_CATALOG = get_builtin_resource_catalog()
BUILTIN_RESOURCE_OPTIONS = sorted(BUILTIN_RESOURCE_CATALOG.keys()) or [""]
MAX_INLINE_RESOURCE_TEXT_CHARS = 12000


def merge_context_field(target, key, value):
    cleaned = str(value or "").strip()
    if cleaned:
        target[key] = cleaned


def append_resource_module(bundle, resource_bundle, role):
    resources = bundle.get("resource_modules", [])
    if not isinstance(resources, list):
        resources = []
    resources.append(
        {
            "resource_key": resource_bundle.get("resource_key", ""),
            "resource_role": role,
            "resource_path": resource_bundle.get("resource_path", ""),
            "description": resource_bundle.get("description", ""),
        }
    )
    bundle["resource_modules"] = resources
    return bundle


def apply_resource_bundle_to_context(bundle, resource_bundle):
    if not isinstance(resource_bundle, dict):
        return bundle
    role = str(resource_bundle.get("resource_role", "")).strip()
    content_text = str(resource_bundle.get("content_text", "")).strip()
    content_json = resource_bundle.get("content_json")

    if role == "task_template" and isinstance(content_json, dict):
        bundle["task_config"] = dict(content_json)
        return bundle
    if role == "task_bundle" and isinstance(content_json, dict):
        bundle["task_bundle"] = dict(content_json)
        return bundle

    if len(content_text) > MAX_INLINE_RESOURCE_TEXT_CHARS:
        return append_resource_module(bundle, resource_bundle, role)

    if role == "system_prompt":
        merge_context_field(bundle, "system_prompt_override", content_text)
    elif role == "character_card":
        merge_context_field(bundle, "character_card_text", content_text)
    elif role == "world_book":
        merge_context_field(bundle, "world_book_text", content_text)
    elif role == "regex_rules":
        merge_context_field(bundle, "regex_rules_text", content_text)
    elif role == "reference_notes":
        merge_context_field(bundle, "extra_notes_text", content_text)
    else:
        append_resource_module(bundle, resource_bundle, role)
    return bundle


def build_task_bundle_struct(
    task_modules,
    format_module,
    translate_direction="",
    style_hint="",
    purpose="",
    fixed_inputs=None,
    resource_modules=None,
):
    task_module_items = []
    for module_name in task_modules:
        module_name = str(module_name or "").strip()
        if not module_name:
            continue
        item = {"type": module_name}
        if module_name == "translate_anime_tags" and str(translate_direction).strip():
            item["direction"] = str(translate_direction).strip()
        task_module_items.append(item)

    payload = {}
    if task_module_items:
        payload["task_modules"] = task_module_items
    if str(format_module or "").strip():
        payload["format_module"] = {"type": str(format_module).strip()}
    if str(style_hint or "").strip() or str(purpose or "").strip():
        payload["task_defaults"] = {}
        if str(style_hint or "").strip():
            payload["task_defaults"]["style_hint"] = str(style_hint).strip()
        if str(purpose or "").strip():
            payload["task_defaults"]["purpose"] = str(purpose).strip()
    if isinstance(fixed_inputs, dict) and fixed_inputs:
        payload["fixed_inputs"] = fixed_inputs
    if isinstance(resource_modules, list) and resource_modules:
        payload["resource_modules"] = resource_modules
    return payload


def build_sequential_payloads(task_bundle, base_inputs, fallback_task_type, fallback_target_profile, fallback_direction, fallback_style_hint, fallback_purpose):
    task_bundle = task_bundle if isinstance(task_bundle, dict) else {}
    modules = task_bundle.get("task_modules", [])
    if not isinstance(modules, list):
        modules = []
    modules = [item for item in modules if isinstance(item, dict) and str(item.get("type", "")).strip()]

    task_defaults = task_bundle.get("task_defaults", {})
    if not isinstance(task_defaults, dict):
        task_defaults = {}

    final_profile = (
        str(task_bundle.get("format_module", {}).get("type", "")).strip()
        if isinstance(task_bundle.get("format_module", {}), dict)
        else ""
    ) or str(fallback_target_profile or "").strip()

    if not modules:
        return []

    payloads = []
    current_seed = ""
    if "raw_text" in base_inputs and str(base_inputs.get("raw_text", "")).strip():
        current_seed = str(base_inputs.get("raw_text", "")).strip()
    elif "raw_tags" in base_inputs and str(base_inputs.get("raw_tags", "")).strip():
        current_seed = str(base_inputs.get("raw_tags", "")).strip()

    global_fixed_inputs = task_bundle.get("fixed_inputs", {})
    if not isinstance(global_fixed_inputs, dict):
        global_fixed_inputs = {}

    for index, module in enumerate(modules):
        module_type = str(module.get("type", "")).strip()
        inputs = dict(base_inputs)
        inputs.update(global_fixed_inputs)
        module_fixed_inputs = module.get("fixed_inputs", {})
        if isinstance(module_fixed_inputs, dict):
            inputs.update(module_fixed_inputs)

        input_field = str(module.get("input_field", "")).strip() or default_task_input_field(module_type)
        if index == 0 and current_seed and input_field:
            inputs[input_field] = current_seed

        resolved_direction = (
            str(module.get("direction", "")).strip()
            or str(task_defaults.get("translate_direction", "")).strip()
            or str(fallback_direction or "").strip()
        )
        resolved_style_hint = (
            str(module.get("style_hint", "")).strip()
            or str(task_defaults.get("style_hint", "")).strip()
            or str(fallback_style_hint or "").strip()
        )
        resolved_purpose = (
            str(module.get("purpose", "")).strip()
            or str(task_defaults.get("purpose", "")).strip()
            or str(fallback_purpose or "").strip()
        )

        if module_type in ("expand_anime_tags", "normalize_anime_tags", "refine_wd14_tags", "generate_natural_caption"):
            if resolved_style_hint and "style_hint" not in inputs:
                inputs["style_hint"] = resolved_style_hint
            if resolved_purpose and "purpose" not in inputs:
                inputs["purpose"] = resolved_purpose
        if module_type == "translate_anime_tags" and resolved_direction and "direction" not in inputs:
            inputs["direction"] = resolved_direction

        is_last = index == len(modules) - 1
        inputs["target_profile"] = final_profile if is_last else "generic_tag_model"
        payloads.append({"task_type": module_type, "inputs": inputs})
    return payloads


def extract_seed_from_response(task_type, response):
    json_result = response.get("json_result", {}) or {}
    if task_type == "translate_anime_tags":
        translated = str(json_result.get("translated_text", "")).strip()
        if translated:
            return translated
        tags = json_result.get("tag_list", [])
        if isinstance(tags, list) and tags:
            return ", ".join([str(item).strip() for item in tags if str(item).strip()])
        return ""
    if task_type == "expand_anime_tags":
        tags = json_result.get("expanded_tags_en", [])
        if isinstance(tags, list) and tags:
            return ", ".join([str(item).strip() for item in tags if str(item).strip()])
        tags = json_result.get("normalized_tags_en", [])
        if isinstance(tags, list) and tags:
            return ", ".join([str(item).strip() for item in tags if str(item).strip()])
        return ""
    if task_type == "normalize_anime_tags":
        tags = json_result.get("recommended_prompt_order_en", [])
        if isinstance(tags, list) and tags:
            return ", ".join([str(item).strip() for item in tags if str(item).strip()])
        tags = json_result.get("normalized_tags_en", [])
        if isinstance(tags, list) and tags:
            return ", ".join([str(item).strip() for item in tags if str(item).strip()])
        return ""
    if task_type in ("extract_tags_from_image", "vision_tagging", "image_captioning", "refine_wd14_tags"):
        tags = json_result.get("expanded_tags_en", [])
        if isinstance(tags, list) and tags:
            return ", ".join([str(item).strip() for item in tags if str(item).strip()])
        tags = json_result.get("normalized_tags_en", [])
        if isinstance(tags, list) and tags:
            return ", ".join([str(item).strip() for item in tags if str(item).strip()])
        return ""
    if task_type == "generate_natural_caption":
        caption = str(json_result.get("caption_long_en", "")).strip() or str(json_result.get("natural_language_en", "")).strip()
        if caption:
            return caption
        return ""
    return ""


def default_task_input_field(task_type):
    mapping = {
        "expand_anime_tags": "raw_tags",
        "normalize_anime_tags": "raw_tags",
        "translate_anime_tags": "raw_text",
        "extract_tags_from_image": "image_path",
        "vision_tagging": "image_path",
        "image_captioning": "image_path",
        "refine_wd14_tags": "raw_tags",
        "generate_natural_caption": "raw_tags",
    }
    return mapping.get(str(task_type or "").strip(), "raw_text")


def inject_optional_context(
    inputs,
    system_prompt_override,
    character_card_text,
    world_book_text,
    regex_rules_text,
    context_bundle_json,
    image_path,
):
    if str(system_prompt_override).strip():
        inputs["system_prompt_override"] = str(system_prompt_override).strip()
    if str(character_card_text).strip():
        inputs["character_card_text"] = str(character_card_text).strip()
    if str(world_book_text).strip():
        inputs["world_book_text"] = str(world_book_text).strip()
    if str(regex_rules_text).strip():
        inputs["regex_rules_text"] = str(regex_rules_text).strip()
    if str(context_bundle_json).strip():
        inputs["context_bundle_json"] = str(context_bundle_json).strip()
    if str(image_path).strip():
        inputs["image_path"] = str(image_path).strip()
    return inputs


class TaskAgentCharacterDesignNode:
    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("nan")

    @classmethod
    def INPUT_TYPES(cls):
        common = build_common_inputs()
        common.update(
            {
                "project_background": (
                    "STRING",
                    {"default": "现代校园恋爱视觉小说。", "multiline": True},
                ),
                "genre": (
                    "STRING",
                    {"default": "galgame, anime, visual novel", "multiline": False},
                ),
                "target_audience": (
                    "STRING",
                    {"default": "二次元男性向", "multiline": False},
                ),
                "visual_style": (
                    "STRING",
                    {
                        "default": "clean anime line art, polished sdxl style",
                        "multiline": True,
                    },
                ),
                "role_archetype": (
                    "STRING",
                    {"default": "嘴硬但心软的学生会副会长", "multiline": True},
                ),
                "constraints": (
                    "STRING",
                    {"default": "避免过度性感，适合立绘设计。", "multiline": True},
                ),
                "reference_notes": (
                    "STRING",
                    {"default": "请输出可用于绘图模型的英文prompt和tags。", "multiline": True},
                ),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05}),
                "max_tokens": ("INT", {"default": 1200, "min": 128, "max": 4096, "step": 32}),
            }
        )
        return {"required": common, "optional": build_legacy_context_optional_inputs()}

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive_prompt", "negative_prompt", "raw_text", "json_text", "status")
    FUNCTION = "run_task"
    CATEGORY = "Task Agent/实验"
    DESCRIPTION = "实验功能：角色设计与长文本策划。当前不是主流程，建议仅在需要时使用。"

    def run_task(
        self,
        gateway_url,
        backend_provider,
        model_source,
        backend_profile,
        custom_model_path,
        custom_mmproj_path,
        context_size,
        llama_cpp_python_n_gpu_layers,
        llama_cpp_python_n_batch,
        llama_cpp_python_threads,
        project_background,
        genre,
        target_audience,
        visual_style,
        role_archetype,
        constraints,
        reference_notes,
        temperature,
        max_tokens,
        auto_load_backend,
        unload_after_run,
        system_prompt_override=None,
        character_card_text=None,
        world_book_text=None,
        regex_rules_text=None,
        context_bundle_json=None,
        image_path=None,
    ):
        inputs = inject_optional_context(
            {
                "project_background": project_background,
                "genre": genre,
                "target_audience": target_audience,
                "visual_style": visual_style,
                "role_archetype": role_archetype,
                "constraints": constraints,
                "reference_notes": reference_notes,
            },
            system_prompt_override,
            character_card_text,
            world_book_text,
            regex_rules_text,
            context_bundle_json,
            image_path,
        )
        try:
            response = run_task_direct(
                gateway_url=gateway_url,
                backend_provider=backend_provider,
                task_type="generate_character_design",
                inputs=inputs,
                temperature=float(temperature),
                max_tokens=int(max_tokens),
                auto_load_backend=bool(auto_load_backend),
                unload_after_run=bool(unload_after_run),
                backend_profile=parse_backend_profile_choice(backend_profile),
                context_size=int(context_size),
                custom_model_path=(str(custom_model_path).strip() if model_source == "custom_path" else ""),
                custom_mmproj_path=str(custom_mmproj_path).strip(),
                runtime_options=build_runtime_options(
                    llama_cpp_python_n_gpu_layers,
                    llama_cpp_python_n_batch,
                    llama_cpp_python_threads,
                ),
            )
        except Exception as error:
            return ("", "", "", "", f"direct_backend_failed: {error}")

        raw_text = response.get("raw_text", "")
        json_result = response.get("json_result", {})
        resolved_request = response.get("resolved_request", {})
        if isinstance(json_result, dict) and isinstance(resolved_request, dict) and resolved_request:
            json_result["_task_agent_debug"] = resolved_request
        json_text = json.dumps(json_result, ensure_ascii=False, indent=2)
        positive_prompt = json_result.get("formatted_prompt", "")
        negative_prompt = json_result.get("formatted_negative_prompt", "")
        status = response.get("status", "unknown")
        return (positive_prompt, negative_prompt, raw_text, json_text, status)


def execute_tag_utility_internal(
    *,
    gateway_url,
    backend_provider,
    model_source,
    backend_profile,
    custom_model_path,
    custom_mmproj_path,
    context_size,
    llama_cpp_python_n_gpu_layers,
    llama_cpp_python_n_batch,
    llama_cpp_python_threads,
    task_type,
    text_input,
    style_hint,
    purpose,
    translate_direction,
    target_profile,
    auto_load_backend,
    unload_after_run,
    context_bundle_json=None,
):
    runtime_options = build_runtime_options(
        llama_cpp_python_n_gpu_layers,
        llama_cpp_python_n_batch,
        llama_cpp_python_threads,
    )
    context_bundle_raw = str(context_bundle_json or "").strip()
    context_bundle = parse_json_dict_or_none(context_bundle_raw) or {}
    task_config = context_bundle.get("task_config", {})
    if not isinstance(task_config, dict):
        task_config = {}
    task_bundle = context_bundle.get("task_bundle", {})
    if not isinstance(task_bundle, dict):
        task_bundle = {}
    if not task_bundle and isinstance(task_config, dict):
        if task_config.get("task_modules") or task_config.get("format_module"):
            task_bundle = dict(task_config)

    resolved_task_type = (
        str(task_config.get("task_type", "")).strip()
        or str(context_bundle.get("task_type", "")).strip()
        or str(task_type).strip()
    )
    resolved_target_profile = (
        str(task_config.get("target_profile", "")).strip()
        or str(context_bundle.get("target_profile", "")).strip()
        or str(target_profile).strip()
    )
    resolved_direction = (
        str(task_config.get("translate_direction", "")).strip()
        or str(context_bundle.get("translate_direction", "")).strip()
        or str(translate_direction).strip()
    )
    resolved_style_hint = (
        str(task_config.get("style_hint", "")).strip()
        or str(context_bundle.get("style_hint", "")).strip()
        or str(style_hint).strip()
    )
    resolved_purpose = (
        str(task_config.get("purpose", "")).strip()
        or str(context_bundle.get("purpose", "")).strip()
        or str(purpose).strip()
    )
    input_field = (
        str(task_config.get("input_field", "")).strip()
        or str(context_bundle.get("input_field", "")).strip()
        or default_task_input_field(resolved_task_type)
    )

    fixed_inputs = {}
    for candidate in (context_bundle.get("fixed_inputs", {}), task_config.get("fixed_inputs", {})):
        if isinstance(candidate, dict):
            fixed_inputs.update({str(key): value for key, value in candidate.items()})

    inputs = dict(fixed_inputs)
    raw_input_value = str(text_input or "").strip()
    if raw_input_value and input_field:
        inputs[input_field] = raw_input_value

    needs_direct_input = not (isinstance(task_bundle, dict) and task_bundle.get("task_modules"))
    if needs_direct_input and input_field and not str(inputs.get(input_field, "")).strip():
        return (
            "",
            "",
            "",
            "",
            f"missing_task_input: field={input_field}. 请在标签工具的 text_input 提供主输入，或在上下文模板 task_config.fixed_inputs 中预置该字段。",
        )

    if resolved_task_type in ("expand_anime_tags", "normalize_anime_tags"):
        if resolved_style_hint and "style_hint" not in inputs:
            inputs["style_hint"] = resolved_style_hint
        if resolved_purpose and "purpose" not in inputs:
            inputs["purpose"] = resolved_purpose
    if resolved_task_type == "translate_anime_tags" and resolved_direction and "direction" not in inputs:
        inputs["direction"] = resolved_direction
    if resolved_target_profile and "target_profile" not in inputs:
        inputs["target_profile"] = resolved_target_profile
    inputs["enable_image_input"] = backend_choice_supports_vision(backend_profile, custom_mmproj_path)
    if context_bundle_raw:
        inputs["context_bundle_json"] = context_bundle_raw
    if str(context_bundle.get("image_path", "")).strip() and "image_path" not in inputs:
        inputs["image_path"] = str(context_bundle.get("image_path", "")).strip()

    response = None
    chain_debug = []

    if isinstance(task_bundle, dict) and task_bundle.get("task_modules"):
        sequential_base_inputs = dict(inputs)
        if raw_input_value and input_field:
            sequential_base_inputs[input_field] = raw_input_value

        payloads = build_sequential_payloads(
            task_bundle,
            sequential_base_inputs,
            resolved_task_type,
            resolved_target_profile,
            resolved_direction,
            resolved_style_hint,
            resolved_purpose,
        )
        if not payloads:
            return ("", "", "", "", "empty_task_bundle")

        for index, payload in enumerate(payloads):
            payload["context_size"] = int(context_size)
            payload["auto_load_backend"] = bool(auto_load_backend)
            payload["unload_after_run"] = bool(unload_after_run) if index == len(payloads) - 1 else False
            payload["temperature"] = 0.4
            task_name = str(payload.get("task_type", "")).strip()
            if task_name in {"extract_tags_from_image", "vision_tagging", "image_captioning", "refine_wd14_tags"}:
                payload["max_tokens"] = 1400
            elif task_name == "generate_natural_caption":
                payload["max_tokens"] = 520
            else:
                payload["max_tokens"] = 900

            if request_needs_mmproj(
                model_source,
                custom_model_path,
                custom_mmproj_path,
                payload.get("task_type", ""),
                payload.get("inputs", {}),
            ):
                return (
                    "",
                    "",
                    "",
                    "",
                    "missing_custom_mmproj_path: 当前任务需要图片输入，并且你选择的是带视觉能力的 Gemma 自定义模型。请填写匹配的 mmproj 文件路径，或改用纯文本任务/纯文本模型。",
                )

            try:
                response = run_task_direct(
                    gateway_url=gateway_url,
                    backend_provider=backend_provider,
                    task_type=str(payload.get("task_type", "")).strip(),
                    inputs=dict(payload.get("inputs", {}) or {}),
                    temperature=float(payload.get("temperature", 0.4)),
                    max_tokens=int(payload.get("max_tokens", 900)),
                    auto_load_backend=bool(payload.get("auto_load_backend", True)),
                    unload_after_run=bool(payload.get("unload_after_run", False)),
                    backend_profile=parse_backend_profile_choice(backend_profile),
                    context_size=int(payload.get("context_size", context_size)),
                    custom_model_path=(str(custom_model_path).strip() if model_source == "custom_path" else ""),
                    custom_mmproj_path=str(custom_mmproj_path).strip(),
                    runtime_options=runtime_options,
                )
            except Exception as error:
                return ("", "", "", "", f"direct_backend_failed: {error}")

            chain_debug.append(
                {
                    "step_index": index + 1,
                    "task_type": payload.get("task_type", ""),
                    "status": response.get("status", "unknown"),
                }
            )

            next_seed = extract_seed_from_response(payload.get("task_type", ""), response or {})
            if next_seed and index + 1 < len(payloads):
                next_payload = payloads[index + 1]
                next_inputs = next_payload.setdefault("inputs", {})
                next_input_field = default_task_input_field(next_payload.get("task_type", ""))
                if next_input_field:
                    next_inputs[next_input_field] = next_seed
    else:
        if request_needs_mmproj(
            model_source,
            custom_model_path,
            custom_mmproj_path,
            resolved_task_type,
            inputs,
        ):
            return (
                "",
                "",
                "",
                "",
                "missing_custom_mmproj_path: 当前任务需要图片输入，并且你选择的是带视觉能力的 Gemma 自定义模型。请填写匹配的 mmproj 文件路径，或改用纯文本任务/纯文本模型。",
            )

        payload = {
            "task_type": resolved_task_type,
            "context_size": int(context_size),
            "auto_load_backend": bool(auto_load_backend),
            "unload_after_run": bool(unload_after_run),
            "temperature": 0.4,
            "max_tokens": 900,
            "inputs": inputs,
        }
        try:
            response = run_task_direct(
                gateway_url=gateway_url,
                backend_provider=backend_provider,
                task_type=resolved_task_type,
                inputs=inputs,
                temperature=float(payload.get("temperature", 0.4)),
                max_tokens=int(payload.get("max_tokens", 900)),
                auto_load_backend=bool(payload.get("auto_load_backend", True)),
                unload_after_run=bool(payload.get("unload_after_run", False)),
                backend_profile=parse_backend_profile_choice(backend_profile),
                context_size=int(payload.get("context_size", context_size)),
                custom_model_path=(str(custom_model_path).strip() if model_source == "custom_path" else ""),
                custom_mmproj_path=str(custom_mmproj_path).strip(),
                runtime_options=runtime_options,
            )
        except Exception as error:
            return ("", "", "", "", f"direct_backend_failed: {error}")

    if response is None:
        return ("", "", "", "", "no_response")

    raw_text = response.get("raw_text", "")
    json_result = response.get("json_result", {})
    if isinstance(json_result, dict) and chain_debug:
        json_result["_task_agent_chain"] = chain_debug
    json_text = json.dumps(json_result, ensure_ascii=False, indent=2)
    positive_prompt = json_result.get("formatted_prompt", "")
    negative_prompt = json_result.get("formatted_negative_prompt", "")
    status = response.get("status", "unknown")
    return (positive_prompt, negative_prompt, raw_text, json_text, status)


class TaskAgentLegacyTagUtilityNode:
    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("nan")

    @classmethod
    def INPUT_TYPES(cls):
        common = build_common_inputs()
        common.update(
            {
                "task_type": (
                    [
                        "expand_anime_tags",
                        "translate_anime_tags",
                        "normalize_anime_tags",
                        "extract_tags_from_image",
                        "refine_wd14_tags",
                        "generate_natural_caption",
                    ],
                ),
                "text_input": (
                    "STRING",
                    {
                        "default": "1girl, school uniform, black hair, stern expression",
                        "multiline": True,
                    },
                ),
                "style_hint": (
                    "STRING",
                    {
                        "default": "anime, galgame, clean sdxl style",
                        "multiline": True,
                    },
                ),
                "purpose": (
                    "STRING",
                    {"default": "用于角色绘图 tag 调试", "multiline": True},
                ),
                "translate_direction": (
                    ["zh_to_en_tags", "en_to_zh_explain"],
                ),
                "target_profile": (
                    [
                        "generic_tag_model",
                        "anima_v1",
                        "anima_train_v1",
                        "illustrious_xl_v01",
                        "illustrious_train_v1",
                        "noobai_xl_1_1",
                        "noobai_train_v1",
                        "newbie_exp01",
                        "newbie_train_xml_v1",
                        "flux_natural_language_v1",
                        "flux_train_nl_v1",
                        "structured_json_v1",
                        "structured_json_train_v1",
                    ],
                ),
            }
        )
        return {"required": common, "optional": build_context_optional_inputs()}

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive_prompt", "negative_prompt", "raw_text", "json_text", "status")
    FUNCTION = "run_task"
    CATEGORY = "Task Agent/兼容旧工作流"
    DESCRIPTION = "兼容旧工作流：保留 task_type / style_hint / target_profile 等旧式直接控制字段。新工作流请优先使用主线版‘任务代理·标签工具’。"

    def run_task(
        self,
        gateway_url,
        backend_provider,
        model_source,
        backend_profile,
        custom_model_path,
        custom_mmproj_path,
        context_size,
        llama_cpp_python_n_gpu_layers,
        llama_cpp_python_n_batch,
        llama_cpp_python_threads,
        task_type,
        text_input,
        style_hint,
        purpose,
        translate_direction,
        target_profile,
        auto_load_backend,
        unload_after_run,
        context_bundle_json=None,
    ):
        return execute_tag_utility_internal(
            gateway_url=gateway_url,
            backend_provider=backend_provider,
            model_source=model_source,
            backend_profile=backend_profile,
            custom_model_path=custom_model_path,
            custom_mmproj_path=custom_mmproj_path,
            context_size=context_size,
            llama_cpp_python_n_gpu_layers=llama_cpp_python_n_gpu_layers,
            llama_cpp_python_n_batch=llama_cpp_python_n_batch,
            llama_cpp_python_threads=llama_cpp_python_threads,
            task_type=task_type,
            text_input=text_input,
            style_hint=style_hint,
            purpose=purpose,
            translate_direction=translate_direction,
            target_profile=target_profile,
            auto_load_backend=auto_load_backend,
            unload_after_run=unload_after_run,
            context_bundle_json=context_bundle_json,
        )


class TaskAgentTagUtilityNode:
    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("nan")

    @classmethod
    def INPUT_TYPES(cls):
        common = build_common_inputs()
        common.update(
            {
                "text_input": (
                    "STRING",
                    {
                        "default": "1girl, school uniform, black hair, stern expression",
                        "multiline": True,
                    },
                ),
            }
        )
        return {"required": common, "optional": build_context_optional_inputs()}

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive_prompt", "negative_prompt", "raw_text", "json_text", "status")
    FUNCTION = "run_task"
    CATEGORY = "Task Agent/标签工具"
    DESCRIPTION = "主线推荐：统一执行器。推荐和‘任务模组拼装器 + 资源加载器 + 上下文拼装’配套使用；本节点只负责执行，不负责定义任务。"

    def run_task(
        self,
        gateway_url,
        backend_provider,
        model_source,
        backend_profile,
        custom_model_path,
        custom_mmproj_path,
        context_size,
        llama_cpp_python_n_gpu_layers,
        llama_cpp_python_n_batch,
        llama_cpp_python_threads,
        text_input,
        auto_load_backend,
        unload_after_run,
        context_bundle_json=None,
    ):
        return execute_tag_utility_internal(
            gateway_url=gateway_url,
            backend_provider=backend_provider,
            model_source=model_source,
            backend_profile=backend_profile,
            custom_model_path=custom_model_path,
            custom_mmproj_path=custom_mmproj_path,
            context_size=context_size,
            llama_cpp_python_n_gpu_layers=llama_cpp_python_n_gpu_layers,
            llama_cpp_python_n_batch=llama_cpp_python_n_batch,
            llama_cpp_python_threads=llama_cpp_python_threads,
            task_type="",
            text_input=text_input,
            style_hint="",
            purpose="",
            translate_direction="",
            target_profile="",
            auto_load_backend=auto_load_backend,
            unload_after_run=unload_after_run,
            context_bundle_json=context_bundle_json,
        )


class TaskAgentOutfitGeneratorNode:
    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("nan")

    @classmethod
    def INPUT_TYPES(cls):
        common = build_common_inputs()
        common.update(
            {
                "character_description": (
                    "STRING",
                    {
                        "default": "高中少女，黑色中长发，冷静认真但有一点笨拙的温柔感。",
                        "multiline": True,
                    },
                ),
                "personality_traits": (
                    "STRING",
                    {
                        "default": "克制、知性、责任感强，带一点不擅长表达的温柔。",
                        "multiline": True,
                    },
                ),
                "outfit_scene": (
                    "STRING",
                    {
                        "default": "放学后去排练室前的日常常服立绘。",
                        "multiline": True,
                    },
                ),
                "season": (
                    "STRING",
                    {"default": "春季", "multiline": False},
                ),
                "style_direction": (
                    "STRING",
                    {
                        "default": "学院感、清爽、日系少女感，不要过于制式校服。",
                        "multiline": True,
                    },
                ),
                "design_constraints": (
                    "STRING",
                    {
                        "default": "保持少女感，不要成熟写实，不要过度暴露；上下装不要冲突。",
                        "multiline": True,
                    },
                ),
                "reference_notes": (
                    "STRING",
                    {
                        "default": "优先使用 Danbooru 服装 tag，利用服装词典做合理选择，不要机械复制模板。",
                        "multiline": True,
                    },
                ),
                "target_profile": (
                    ["generic_tag_model", "anima_v1", "illustrious_xl_v01", "noobai_xl_1_1", "newbie_exp01"],
                ),
                "temperature": ("FLOAT", {"default": 0.55, "min": 0.0, "max": 2.0, "step": 0.05}),
                "max_tokens": ("INT", {"default": 1200, "min": 128, "max": 4096, "step": 32}),
            }
        )
        return {"required": common, "optional": build_legacy_context_optional_inputs()}

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive_prompt", "negative_prompt", "raw_text", "json_text", "status")
    FUNCTION = "run_task"
    CATEGORY = "Task Agent/实验"
    DESCRIPTION = "实验功能：服装标签生成。当前保留为可选用法，不作为主功能入口。"

    def run_task(
        self,
        gateway_url,
        backend_provider,
        model_source,
        backend_profile,
        custom_model_path,
        custom_mmproj_path,
        context_size,
        llama_cpp_python_n_gpu_layers,
        llama_cpp_python_n_batch,
        llama_cpp_python_threads,
        character_description,
        personality_traits,
        outfit_scene,
        season,
        style_direction,
        design_constraints,
        reference_notes,
        target_profile,
        temperature,
        max_tokens,
        auto_load_backend,
        unload_after_run,
        system_prompt_override=None,
        character_card_text=None,
        world_book_text=None,
        regex_rules_text=None,
        context_bundle_json=None,
        image_path=None,
    ):
        inputs = inject_optional_context(
            {
                "character_description": character_description,
                "personality_traits": personality_traits,
                "outfit_scene": outfit_scene,
                "season": season,
                "style_direction": style_direction,
                "design_constraints": design_constraints,
                "reference_notes": reference_notes,
                "target_profile": target_profile,
            },
            system_prompt_override,
            character_card_text,
            world_book_text,
            regex_rules_text,
            context_bundle_json,
            image_path,
        )
        try:
            response = run_task_direct(
                gateway_url=gateway_url,
                backend_provider=backend_provider,
                task_type="generate_outfit_tags",
                inputs=inputs,
                temperature=float(temperature),
                max_tokens=int(max_tokens),
                auto_load_backend=bool(auto_load_backend),
                unload_after_run=bool(unload_after_run),
                backend_profile=parse_backend_profile_choice(backend_profile),
                context_size=int(context_size),
                custom_model_path=(str(custom_model_path).strip() if model_source == "custom_path" else ""),
                custom_mmproj_path=str(custom_mmproj_path).strip(),
                runtime_options=build_runtime_options(
                    llama_cpp_python_n_gpu_layers,
                    llama_cpp_python_n_batch,
                    llama_cpp_python_threads,
                ),
            )
        except Exception as error:
            return ("", "", "", "", f"direct_backend_failed: {error}")

        raw_text = response.get("raw_text", "")
        json_result = response.get("json_result", {})
        json_text = json.dumps(json_result, ensure_ascii=False, indent=2)
        positive_prompt = json_result.get("formatted_prompt", "")
        negative_prompt = json_result.get("formatted_negative_prompt", "")
        status = response.get("status", "unknown")
        return (positive_prompt, negative_prompt, raw_text, json_text, status)


class TaskAgentFileConfigLoaderNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "file_path": ("STRING", {"default": "", "multiline": False}),
                "parse_mode": (["auto", "text", "json"],),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("text_content", "json_text", "status")
    FUNCTION = "load_file"
    CATEGORY = "Task Agent/配置"
    DESCRIPTION = "读取角色卡、世界书、系统提示词、正则配置等本地文件。"

    def load_file(self, file_path, parse_mode):
        path = Path(str(file_path).strip())
        if not path.exists():
            return ("", "", f"missing_file: {path}")

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")

        json_text = ""
        if parse_mode in ("auto", "json") and path.suffix.lower() in {".json", ".jsonl"}:
            try:
                if path.suffix.lower() == ".jsonl":
                    payload = [json.loads(line) for line in text.splitlines() if line.strip()]
                else:
                    payload = json.loads(text)
                json_text = json.dumps(payload, ensure_ascii=False, indent=2)
            except Exception as error:
                if parse_mode == "json":
                    return (text, "", f"json_parse_failed: {error}")

        return (text, json_text, "ok")


class TaskAgentResourceLoaderNode:
    @classmethod
    def INPUT_TYPES(cls):
        role_options = [
            "",
            "system_prompt",
            "character_card",
            "world_book",
            "regex_rules",
            "reference_notes",
            "task_template",
            "task_bundle",
            "danbooru_character_dict",
            "danbooru_character_corpus",
            "danbooru_clothing_dict",
            "danbooru_cooccurrence",
            "danbooru_tag_stats",
            "danbooru_character_safety",
        ]
        return {
            "required": {
                "resource_source": (["builtin_catalog", "custom_path"],),
                "builtin_resource_key": (BUILTIN_RESOURCE_OPTIONS,),
                "custom_resource_path": ("STRING", {"default": "", "multiline": False}),
                "resource_role_override": (role_options,),
                "parse_mode": (["auto", "path", "text", "json", "jsonl"],),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("resource_text", "resource_json", "resource_bundle_json", "status")
    FUNCTION = "load_resource"
    CATEGORY = "Task Agent/配置"
    DESCRIPTION = "加载内置或外部资源文件，输出可连线的 resource_bundle_json，供上下文拼装和后续任务使用。"

    def load_resource(
        self,
        resource_source,
        builtin_resource_key,
        custom_resource_path,
        resource_role_override,
        parse_mode,
    ):
        selected_key = str(builtin_resource_key or "").strip()
        bundle = {}
        status_parts = []

        if resource_source == "builtin_catalog":
            spec = BUILTIN_RESOURCE_CATALOG.get(selected_key)
            if not spec:
                return ("", "", "", f"missing_builtin_resource: {selected_key}")
            resource_path = str(spec.get("path", "")).strip()
            resource_role = str(spec.get("resource_role", "")).strip()
            effective_parse_mode = str(spec.get("parse_mode", "text")).strip() or "text"
            description = str(spec.get("description", "")).strip()
            resource_key = selected_key
        else:
            resource_path = str(custom_resource_path or "").strip()
            if not resource_path:
                return ("", "", "", "missing_custom_resource_path")
            resource_role = str(resource_role_override or "").strip()
            effective_parse_mode = str(parse_mode or "auto").strip() or "auto"
            description = ""
            resource_key = ""

        path = Path(resource_path)
        if not path.exists():
            return ("", "", "", f"missing_file: {path}")

        json_value = None
        json_text = ""
        detected_mode = effective_parse_mode
        if effective_parse_mode == "auto":
            suffix = path.suffix.lower()
            if suffix == ".json":
                detected_mode = "json"
            elif suffix == ".jsonl":
                detected_mode = "jsonl"
            else:
                detected_mode = "text"

        text = ""
        if detected_mode != "path":
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8", errors="replace")

        try:
            if detected_mode == "json":
                json_value = json.loads(text)
                json_text = json.dumps(json_value, ensure_ascii=False, indent=2)
            elif detected_mode == "jsonl":
                json_value = [json.loads(line) for line in text.splitlines() if line.strip()]
                json_text = json.dumps(json_value, ensure_ascii=False, indent=2)
        except Exception as error:
            if effective_parse_mode in {"json", "jsonl"}:
                return (text, "", "", f"{detected_mode}_parse_failed: {error}")
            status_parts.append(f"{detected_mode}_parse_failed: {error}")

        if not resource_role:
            resource_role = "reference_notes"

        bundle = {
            "resource_key": resource_key,
            "resource_path": str(path),
            "resource_role": resource_role,
            "description": description,
            "parse_mode": detected_mode,
            "content_text": str(text).strip(),
            "content_json": json_value,
        }
        status_parts.append("ok")
        return (
            text,
            json_text,
            json.dumps(bundle, ensure_ascii=False, indent=2),
            " | ".join([part for part in status_parts if part]),
        )


class TaskAgentResourceBundleMergeNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "resource_bundle_json_1": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "resource_bundle_json_2": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "resource_bundle_json_3": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "resource_bundle_json_4": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("resource_bundle_json", "status")
    FUNCTION = "merge"
    CATEGORY = "Task Agent/配置"
    DESCRIPTION = "合并多个资源加载器输出，便于同时挂载任务链预设、词典、世界书、参考说明等上下文资源。"

    def merge(
        self,
        resource_bundle_json_1,
        resource_bundle_json_2,
        resource_bundle_json_3,
        resource_bundle_json_4,
    ):
        bundles = []
        errors = []
        for index, raw in enumerate(
            [
                resource_bundle_json_1,
                resource_bundle_json_2,
                resource_bundle_json_3,
                resource_bundle_json_4,
            ],
            start=1,
        ):
            text = str(raw or "").strip()
            if not text:
                continue
            parsed = parse_json_value_or_none(text)
            if isinstance(parsed, dict):
                bundles.append(parsed)
            elif isinstance(parsed, list):
                bundles.extend([item for item in parsed if isinstance(item, dict)])
            else:
                errors.append(f"input_{index}_not_json_bundle")
        status = f"ok count={len(bundles)}" if bundles else "empty"
        if errors:
            status += " | " + "; ".join(errors)
        return (json.dumps(bundles, ensure_ascii=False, indent=2), status)


class TaskAgentTaskModuleComposerNode:
    @classmethod
    def INPUT_TYPES(cls):
        task_options = [
            "",
            "translate_anime_tags",
            "expand_anime_tags",
            "normalize_anime_tags",
            "extract_tags_from_image",
            "refine_wd14_tags",
            "generate_natural_caption",
        ]
        format_options = [
            "",
            "generic_tag_model",
            "anima_v1",
            "anima_train_v1",
            "illustrious_xl_v01",
            "illustrious_train_v1",
            "noobai_xl_1_1",
            "noobai_train_v1",
            "newbie_exp01",
            "newbie_train_xml_v1",
            "flux_natural_language_v1",
            "flux_train_nl_v1",
            "structured_json_v1",
            "structured_json_train_v1",
        ]
        return {
            "required": {
                "task_module_1": (task_options,),
                "task_module_2": (task_options,),
                "task_module_3": (task_options,),
                "task_module_4": (task_options,),
                "format_module": (format_options,),
                "translate_direction": (["", "zh_to_en_tags", "en_to_zh_explain"],),
                "style_hint": ("STRING", {"default": "", "multiline": True}),
                "purpose": ("STRING", {"default": "", "multiline": True}),
                "fixed_inputs_text": ("STRING", {"default": "", "multiline": True}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("task_bundle_json", "status")
    FUNCTION = "compose_task_bundle"
    CATEGORY = "Task Agent/配置"
    DESCRIPTION = "把中文转英文、扩写、规范化、最终格式化等任务模组组装成可连线的 task_bundle_json。"

    def compose_task_bundle(
        self,
        task_module_1,
        task_module_2,
        task_module_3,
        task_module_4,
        format_module,
        translate_direction,
        style_hint,
        purpose,
        fixed_inputs_text,
    ):
        fixed_inputs = parse_json_dict_or_none(fixed_inputs_text) or {}
        bundle = build_task_bundle_struct(
            [task_module_1, task_module_2, task_module_3, task_module_4],
            format_module,
            translate_direction=translate_direction,
            style_hint=style_hint,
            purpose=purpose,
            fixed_inputs=fixed_inputs,
        )
        status = "ok" if bundle.get("task_modules") else "empty_task_modules"
        return (json.dumps(bundle, ensure_ascii=False, indent=2), status)


class TaskAgentLegacyContextComposerNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "task_type_override": (
                    [
                        "",
                        "expand_anime_tags",
                        "translate_anime_tags",
                        "normalize_anime_tags",
                        "generate_outfit_tags",
                        "generate_character_design",
                    ],
                ),
                "system_prompt_text": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "character_card_text": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "world_book_text": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "regex_rules_text": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "extra_notes_text": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "task_config_text": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "system_prompt_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "character_card_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "world_book_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "regex_rules_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "task_config_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "task_bundle_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "resource_bundle_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "context_bundle_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "image_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "task_bundle_json": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "resource_bundle_json": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("context_bundle_json", "status")
    FUNCTION = "compose"
    CATEGORY = "Task Agent/兼容旧工作流"
    DESCRIPTION = "兼容旧工作流：支持旧版 task_type/task_config 混合输入。新工作流请优先使用主线版‘任务代理·上下文拼装’。"

    def compose(
        self,
        task_type_override,
        system_prompt_text,
        character_card_text,
        world_book_text,
        regex_rules_text,
        extra_notes_text,
        task_config_text,
        system_prompt_path,
        character_card_path,
        world_book_path,
        regex_rules_path,
        task_config_path,
        task_bundle_path,
        resource_bundle_path,
        context_bundle_path,
        image_path,
        task_bundle_json,
        resource_bundle_json,
    ):
        return compose_context_bundle_internal(
            task_type_override=task_type_override,
            system_prompt_text=system_prompt_text,
            character_card_text=character_card_text,
            world_book_text=world_book_text,
            regex_rules_text=regex_rules_text,
            extra_notes_text=extra_notes_text,
            task_config_text=task_config_text,
            system_prompt_path=system_prompt_path,
            character_card_path=character_card_path,
            world_book_path=world_book_path,
            regex_rules_path=regex_rules_path,
            task_config_path=task_config_path,
            task_bundle_path=task_bundle_path,
            resource_bundle_path=resource_bundle_path,
            context_bundle_path=context_bundle_path,
            image_path=image_path,
            task_bundle_json=task_bundle_json,
            resource_bundle_json=resource_bundle_json,
        )


class TaskAgentContextComposerNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "system_prompt_text": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "character_card_text": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "world_book_text": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "regex_rules_text": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "extra_notes_text": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "system_prompt_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "character_card_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "world_book_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "regex_rules_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "image_path": ("STRING", {"default": "", "multiline": False, "defaultInput": True}),
                "task_bundle_json": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
                "resource_bundle_json": ("STRING", {"default": "", "multiline": True, "defaultInput": True}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("context_bundle_json", "status")
    FUNCTION = "compose"
    CATEGORY = "Task Agent/配置"
    DESCRIPTION = "主线推荐：用于合并任务模组、资源模组以及 system prompt / 角色卡 / 世界书 / 正则等上下文。"

    def compose(
        self,
        system_prompt_text,
        character_card_text,
        world_book_text,
        regex_rules_text,
        extra_notes_text,
        system_prompt_path,
        character_card_path,
        world_book_path,
        regex_rules_path,
        image_path,
        task_bundle_json,
        resource_bundle_json,
    ):
        return compose_context_bundle_internal(
            system_prompt_text=system_prompt_text,
            character_card_text=character_card_text,
            world_book_text=world_book_text,
            regex_rules_text=regex_rules_text,
            extra_notes_text=extra_notes_text,
            system_prompt_path=system_prompt_path,
            character_card_path=character_card_path,
            world_book_path=world_book_path,
            regex_rules_path=regex_rules_path,
            image_path=image_path,
            task_bundle_json=task_bundle_json,
            resource_bundle_json=resource_bundle_json,
        )


class TaskAgentImagePathBridgeNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "file_basename": ("STRING", {"default": "task_agent_image", "multiline": False}),
                "image_format": (["png", "webp"],),
                "cleanup_enabled": ("BOOLEAN", {"default": True}),
                "max_keep_files": ("INT", {"default": 50, "min": 1, "max": 5000, "step": 1}),
                "max_file_age_hours": ("INT", {"default": 24, "min": 1, "max": 720, "step": 1}),
            }
        }

    RETURN_TYPES = ("STRING", "IMAGE", "STRING")
    RETURN_NAMES = ("image_path", "image_passthrough", "status")
    FUNCTION = "save_image"
    CATEGORY = "Task Agent/桥接"
    DESCRIPTION = "把 ComfyUI 的 IMAGE 落成临时文件路径，供视觉模型或打标任务读取，并可自动清理旧临时图。"

    def save_image(self, image, file_basename, image_format, cleanup_enabled, max_keep_files, max_file_age_hours):
        import numpy as np
        from PIL import Image

        runtime_dir = PROJECT_DIR / "runtime" / "images"
        runtime_dir.mkdir(parents=True, exist_ok=True)

        tensor = image[0].cpu().numpy()
        array = np.clip(tensor * 255.0, 0, 255).astype(np.uint8)
        output_path = runtime_dir / f"{file_basename}_{uuid.uuid4().hex[:8]}.{image_format}"
        pil_image = Image.fromarray(array)
        save_kwargs = {}
        if image_format == "webp":
            save_kwargs["quality"] = 95
        pil_image.save(output_path, **save_kwargs)

        cleanup_notes = []
        if cleanup_enabled:
            now_ts = time.time()
            max_age_seconds = int(max_file_age_hours) * 3600
            deleted_count = 0
            file_items = []
            for candidate in runtime_dir.iterdir():
                if not candidate.is_file():
                    continue
                suffix = candidate.suffix.lower()
                if suffix not in {".png", ".webp", ".jpg", ".jpeg"}:
                    continue
                try:
                    stat = candidate.stat()
                except OSError:
                    continue
                file_items.append((candidate, stat.st_mtime))

            for candidate, modified_ts in file_items:
                if candidate == output_path:
                    continue
                if now_ts - modified_ts <= max_age_seconds:
                    continue
                try:
                    candidate.unlink()
                    deleted_count += 1
                except OSError:
                    pass

            remaining_items = []
            for candidate in runtime_dir.iterdir():
                if not candidate.is_file():
                    continue
                suffix = candidate.suffix.lower()
                if suffix not in {".png", ".webp", ".jpg", ".jpeg"}:
                    continue
                try:
                    stat = candidate.stat()
                except OSError:
                    continue
                remaining_items.append((candidate, stat.st_mtime))

            remaining_items.sort(key=lambda item: item[1], reverse=True)
            overflow_items = remaining_items[int(max_keep_files):]
            for candidate, _ in overflow_items:
                if candidate == output_path:
                    continue
                try:
                    candidate.unlink()
                    deleted_count += 1
                except OSError:
                    pass

            cleanup_notes.append(f"cleanup_deleted={deleted_count}")
            cleanup_notes.append(f"keep_limit={int(max_keep_files)}")
            cleanup_notes.append(f"max_age_hours={int(max_file_age_hours)}")

        status = "ok"
        if cleanup_notes:
            status = status + " | " + ", ".join(cleanup_notes)
        return (str(output_path), image, status)


NODE_CLASS_MAPPINGS = {
    "TaskAgentCharacterDesignNode": TaskAgentCharacterDesignNode,
    "TaskAgentTagUtilityNode": TaskAgentTagUtilityNode,
    "TaskAgentLegacyTagUtilityNode": TaskAgentLegacyTagUtilityNode,
    "TaskAgentOutfitGeneratorNode": TaskAgentOutfitGeneratorNode,
    "TaskAgentFileConfigLoaderNode": TaskAgentFileConfigLoaderNode,
    "TaskAgentResourceLoaderNode": TaskAgentResourceLoaderNode,
    "TaskAgentResourceBundleMergeNode": TaskAgentResourceBundleMergeNode,
    "TaskAgentTaskModuleComposerNode": TaskAgentTaskModuleComposerNode,
    "TaskAgentContextComposerNode": TaskAgentContextComposerNode,
    "TaskAgentLegacyContextComposerNode": TaskAgentLegacyContextComposerNode,
    "TaskAgentImagePathBridgeNode": TaskAgentImagePathBridgeNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "TaskAgentCharacterDesignNode": "任务代理·角色设计（实验）",
    "TaskAgentTagUtilityNode": "任务代理·标签工具",
    "TaskAgentLegacyTagUtilityNode": "任务代理·标签工具（兼容旧工作流）",
    "TaskAgentOutfitGeneratorNode": "任务代理·服装生成（实验）",
    "TaskAgentFileConfigLoaderNode": "任务代理·文件配置加载",
    "TaskAgentResourceLoaderNode": "任务代理·资源加载器",
    "TaskAgentResourceBundleMergeNode": "任务代理·资源包合并",
    "TaskAgentTaskModuleComposerNode": "任务代理·任务模组拼装器",
    "TaskAgentContextComposerNode": "任务代理·上下文拼装",
    "TaskAgentLegacyContextComposerNode": "任务代理·上下文拼装（兼容旧工作流）",
    "TaskAgentImagePathBridgeNode": "任务代理·图片路径桥接",
}
