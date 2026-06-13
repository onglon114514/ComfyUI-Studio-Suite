import argparse
import ipaddress
import importlib.util
import json
import os
import re
import sys
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
        "queue_nodes.py",
        "smart_fill_crop_resize_node.py",
        "config/task_agent_config.example.json",
        "config/backend_profiles.example.json",
        "resources/task_templates/README.md",
        "resources/task_bundles/README.md",
        "docs/SINGLE_RUN_CONTEXT_GUIDE.md",
        "docs/INPROCESS_PERFORMANCE_GUIDE.md",
    ]
    for item in required:
        path = PROJECT_DIR / item
        if path.exists():
            report.note(f"found {item}")
        else:
            report.error(f"missing required file: {item}")


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


def check_python_imports(report):
    sys.path.insert(0, str(PROJECT_DIR))
    try:
        import task_agent_core.nodes as nodes

        report.note(f"node classes: {len(nodes.NODE_CLASS_MAPPINGS)}")
    except Exception as error:
        report.error(f"failed to import task_agent_core.nodes: {error}")

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
    check_json_files(report)
    check_backend_profiles(report)
    check_resources(report)
    check_python_imports(report)
    check_release_paths(report)
    report.print()
    if report.errors or (args.strict and report.warnings):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
