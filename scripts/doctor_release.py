import argparse
import ipaddress
import importlib.util
import json
import os
import re
import sys
import types
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
LOCAL_PATH_PATTERNS = [
    re.compile(r"^[A-Za-z]:[\\/]"),
    re.compile(r"^/home/"),
    re.compile(r"^/data/"),
]


def rel(path):
    try:
        return str(Path(path).resolve().relative_to(PROJECT_DIR.resolve()))
    except Exception:
        return str(path)


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.info = []

    def error(self, message):
        self.errors.append(message)

    def warn(self, message):
        self.warnings.append(message)

    def note(self, message):
        self.info.append(message)

    def print(self):
        for message in self.info:
            print(f"[info] {message}")
        for message in self.warnings:
            print(f"[warn] {message}")
        for message in self.errors:
            print(f"[error] {message}")
        print(json.dumps({"errors": len(self.errors), "warnings": len(self.warnings)}, ensure_ascii=False))


def load_json(path, report):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        report.error(f"invalid_json: {rel(path)}: {error}")
        return None


def is_local_absolute_path(value):
    text = str(value or "").strip()
    return any(pattern.search(text) for pattern in LOCAL_PATH_PATTERNS)


def check_required_files(report):
    required = [
        "__init__.py",
        "task_agent_gateway.py",
        "task_agent_core/nodes.py",
        "task_agent_core/private_llama_worker.py",
        "queue_nodes.py",
        "smart_fill_crop_resize_node.py",
        "prompt_studio/api.py",
        "prompt_studio/local_dictionary.py",
        "prompt_studio/nodes.py",
        "prompt_studio/i18n.json",
        "web/js/prompt_studio_nodes.js",
        "prompt_studio/frontend/index.html",
        "prompt_studio/frontend/js/studioSuiteLlmTools.js",
        "prompt_studio/bundle/main.entry.js",
        "config/task_agent_config.example.json",
        "config/backend_profiles.example.json",
        "config/model_downloads.json",
        "scripts/build_release_preview.py",
        "scripts/check_node_registry.py",
        "scripts/install_min_llm_model.py",
        "scripts/build_local_dictionary_index.py",
        "resources/task_templates/README.md",
        "resources/task_bundles/README.md",
        "docs/SINGLE_RUN_CONTEXT_GUIDE.md",
        "docs/INPROCESS_PERFORMANCE_GUIDE.md",
        "docs/NODE_USER_GUIDE_zh-CN.md",
        "THIRD_PARTY_NOTICES.md",
    ]
    for item in required:
        path = PROJECT_DIR / item
        if path.exists():
            report.note(f"found {item}")
        else:
            report.error(f"missing required file: {item}")


def check_web_extension_surface(report):
    allowed = {
        "web/js/color_widget.js",
        "web/js/prompt_studio_controller.js",
        "web/js/prompt_studio_nodes.js",
        "web/js/task_agent_dynamic_slots.js",
        "web/js/task_agent_live_monitor.js",
    }
    discovered = {
        path.relative_to(PROJECT_DIR).as_posix()
        for path in (PROJECT_DIR / "web").rglob("*.js")
    }
    unexpected = sorted(discovered - allowed)
    missing = sorted(allowed - discovered)
    if unexpected:
        report.error(
            "unexpected JavaScript under WEB_DIRECTORY; ComfyUI auto-loads every .js recursively: "
            + ", ".join(unexpected)
        )
    if missing:
        report.error("missing ComfyUI web entry module: " + ", ".join(missing))
    if not unexpected and not missing:
        report.note(f"ComfyUI web surface ok: {len(discovered)} intentional JavaScript modules")

    deprecated_imports = ("/scripts/ui.js", "/extensions/core/")
    for item in sorted(discovered):
        text = (PROJECT_DIR / item).read_text(encoding="utf-8", errors="replace")
        for deprecated in deprecated_imports:
            if deprecated in text:
                report.error(f"deprecated ComfyUI frontend import in {item}: {deprecated}")


def check_json_files(report):
    for path in [
        *(PROJECT_DIR / "config").glob("*.json"),
        *(PROJECT_DIR / "resources" / "task_templates").glob("*.json"),
        *(PROJECT_DIR / "resources" / "task_bundles").glob("*.json"),
    ]:
        load_json(path, report)


def check_backend_profiles(report):
    profiles_path = PROJECT_DIR / "config" / "backend_profiles.json"
    example_path = PROJECT_DIR / "config" / "backend_profiles.example.json"
    profiles = load_json(profiles_path, report) if profiles_path.exists() else {}
    example = load_json(example_path, report) if example_path.exists() else {}
    if not isinstance(profiles, dict):
        profiles = {}
    if not isinstance(example, dict):
        example = {}
    if example:
        report.note(f"example backend profiles: {', '.join(example.keys())}")
    if not profiles_path.exists() and example:
        report.warn("config/backend_profiles.json is missing; runtime will fall back to backend_profiles.example.json. Copy it to backend_profiles.json before editing model paths.")
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            report.warn(f"backend profile is not object: {name}")
            continue
        for key in ("model_path", "mmproj_path"):
            value = str(profile.get(key, "") or "").strip()
            if not value:
                continue
            if is_local_absolute_path(value):
                report.warn(f"local absolute path in config/backend_profiles.json: {name}.{key}={value}")
            resolved = PROJECT_DIR / value if not Path(value).is_absolute() else Path(value)
            if not resolved.exists():
                report.warn(f"model path not found for current machine: {name}.{key}={value}")

    min_model = PROJECT_DIR / "models" / "llm" / "gemma-4-e2b-hauhau-q8" / "Gemma-4-E2B-Uncensored-HauhauCS-Aggressive-Q8_K_P.gguf"
    configured_min_model = None
    for source in (profiles, example):
        profile = source.get("gemma4_e2b_hauhau_q8") if isinstance(source, dict) else None
        if not isinstance(profile, dict):
            continue
        value = str(profile.get("model_path", "") or "").strip()
        if not value:
            continue
        resolved = PROJECT_DIR / value if not Path(value).is_absolute() else Path(value)
        if resolved.exists():
            configured_min_model = resolved
            break

    if configured_min_model is not None:
        report.note(f"minimum LLM model configured: {rel(configured_min_model)}")
    elif min_model.exists():
        report.note(f"minimum LLM model present: {rel(min_model)}")
    else:
        report.warn(
            "minimum LLM model missing. Prompt Studio LLM translation needs a local GGUF model. "
            "Run: python scripts/install_min_llm_model.py --manual "
            "or place the model at models/llm/gemma-4-e2b-hauhau-q8/"
            "Gemma-4-E2B-Uncensored-HauhauCS-Aggressive-Q8_K_P.gguf"
        )


def check_task_agent_config(report):
    local_path = PROJECT_DIR / "config" / "task_agent_config.local.json"
    example_path = PROJECT_DIR / "config" / "task_agent_config.example.json"
    local_config = load_json(local_path, report) if local_path.exists() else None
    example_config = load_json(example_path, report) if example_path.exists() else None

    active = local_config if isinstance(local_config, dict) else example_config
    active_name = "config/task_agent_config.local.json" if isinstance(local_config, dict) else "config/task_agent_config.example.json"
    if active is None:
        report.error("missing usable task agent config: config/task_agent_config.local.json or config/task_agent_config.example.json")
        return

    if not local_path.exists():
        report.warn("config/task_agent_config.local.json is missing; runtime will fall back to task_agent_config.example.json. Copy it before changing backend defaults.")
    backend = active.get("backend", {}) if isinstance(active, dict) else {}
    if not isinstance(backend, dict):
        report.error(f"invalid backend config in {active_name}")
        return
    report.note(
        "active task config: "
        f"{active_name} provider={backend.get('provider', '<missing>')} mode={backend.get('mode', '<missing>')}"
    )


def check_resources(report):
    resources = {
        "resources/danbooru_character_aliases.json": "small required alias override",
        "resources/character_alias_safety.json": "small required alias safety rules",
        "resources/task_templates": "task templates",
        "resources/task_bundles": "task bundles",
    }
    for item, purpose in resources.items():
        path = PROJECT_DIR / item
        if not path.exists():
            report.error(f"missing resource: {item} ({purpose})")
        else:
            report.note(f"resource ok: {item}")

    optional_large = [
        "danbooru_character_aliases.generated.json",
        "danbooru_character_webui.normalized.jsonl",
        "tag_count_tags_统计.jsonl",
        "danbooru_tags_cooccurrence.csv",
        "danbooru_artist_wildcard-D站画师列表.txt",
    ]
    for name in optional_large:
        path = PROJECT_DIR / "resources" / name
        if not path.exists():
            report.warn(f"optional large resource missing: resources/{name}")
        else:
            report.note(f"optional resource present: resources/{name} ({path.stat().st_size // 1024 // 1024} MB)")


def find_comfy_root():
    candidates = [*PROJECT_DIR.parents]
    configured = str(os.environ.get("COMFYUI_ROOT", "") or "").strip()
    if configured:
        candidates.insert(0, Path(configured).expanduser())
    current = Path.cwd().resolve()
    candidates.extend([current, *current.parents])
    for candidate in dict.fromkeys(candidates):
        if (candidate / "server.py").exists() and (candidate / "utils" / "install_util.py").exists():
            return candidate
    return None


def ensure_comfy_root_on_path(report):
    comfy_root = find_comfy_root()
    if comfy_root is None:
        report.warn("ComfyUI root was not found above this project; using lightweight node import")
        return None

    root_text = str(comfy_root)
    if root_text in sys.path:
        sys.path.remove(root_text)
    sys.path.insert(0, root_text)

    existing_utils = sys.modules.get("utils")
    if existing_utils is not None and not hasattr(existing_utils, "__path__"):
        sys.modules.pop("utils", None)
    try:
        import utils.install_util  # noqa: F401
        report.note(f"ComfyUI root ok: {comfy_root}")
    except Exception as error:
        report.warn(f"ComfyUI utils import check failed: {error}")
    return comfy_root


def import_nodes_lightweight():
    package = types.ModuleType("task_agent_core")
    package.__path__ = [str(PROJECT_DIR / "task_agent_core")]
    sys.modules["task_agent_core"] = package
    spec = importlib.util.spec_from_file_location(
        "task_agent_core.nodes",
        str(PROJECT_DIR / "task_agent_core" / "nodes.py"),
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to build import spec for task_agent_core/nodes.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["task_agent_core.nodes"] = module
    spec.loader.exec_module(module)
    return module


def import_full_plugin_registry():
    init_path = PROJECT_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "comfyui_studio_suite_doctor_import",
        str(init_path),
        submodule_search_locations=[str(PROJECT_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to build import spec for __init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check_python_imports(report):
    comfy_root = ensure_comfy_root_on_path(report)
    project_text = str(PROJECT_DIR)
    if project_text in sys.path:
        sys.path.remove(project_text)
    sys.path.insert(1 if comfy_root is not None else 0, project_text)
    try:
        if comfy_root is None:
            nodes = import_nodes_lightweight()
            report.note(f"task agent node classes: {len(nodes.NODE_CLASS_MAPPINGS)}")
        else:
            plugin = import_full_plugin_registry()
            report.note(f"registered node classes: {len(plugin.NODE_CLASS_MAPPINGS)}")
    except Exception as error:
        report.error(f"failed to import node registry: {error}")

    private_llama = PROJECT_DIR / "runtime" / "python_libs" / "llama_cpp_python_cu130"
    if private_llama.exists():
        sys.path.insert(0, str(private_llama))
        try:
            spec = importlib.util.find_spec("llama_cpp")
            if spec and spec.origin:
                report.note(f"private llama_cpp available: {spec.origin}")
            else:
                report.warn("private llama_cpp path exists but module was not found")
        except Exception as error:
            report.warn(f"private llama_cpp check failed: {error}")
    else:
        report.warn("private llama-cpp-python runtime not bundled: runtime/python_libs/llama_cpp_python_cu130")


def check_release_paths(report):
    scan_files = [
        PROJECT_DIR / "README.md",
        PROJECT_DIR / "config" / "resource_manifest.json",
        PROJECT_DIR / "config" / "backend_profiles.json",
        PROJECT_DIR / "config" / "task_agent_config.local.json",
    ]
    for path in scan_files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        has_secret_like_assignment = re.search(
            r"(?i)(password|passwd|secret|token)\s*[:=]\s*['\"][^'\"]{8,}",
            text,
        )
        has_ip_literal = False
        for match in re.finditer(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
            try:
                ip_value = ipaddress.ip_address(match.group(0))
            except ValueError:
                continue
            if not (ip_value.is_loopback or ip_value.is_private or ip_value.is_link_local):
                has_ip_literal = True
                break
        if has_secret_like_assignment or has_ip_literal:
            report.error(f"possible secret/server credential in {rel(path)}")
        if re.search(r"(?i)\b[A-Z]:[\\/].*(llm|model|kobold|comfy)", text):
            report.warn(f"local model path reference in {rel(path)}")


def main():
    parser = argparse.ArgumentParser(description="ComfyUI Studio Suite release/install self-check.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero when warnings exist.")
    args = parser.parse_args()

    report = Report()
    report.note(f"project_dir={PROJECT_DIR}")
    check_required_files(report)
    check_web_extension_surface(report)
    check_json_files(report)
    check_backend_profiles(report)
    check_task_agent_config(report)
    check_resources(report)
    check_python_imports(report)
    check_release_paths(report)
    report.print()
    if report.errors or (args.strict and report.warnings):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
