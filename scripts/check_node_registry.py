from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


def _find_comfy_root() -> Path | None:
    candidates: list[Path] = [*PROJECT_DIR.parents]
    configured = str(os.environ.get("COMFYUI_ROOT", "") or "").strip()
    if configured:
        candidates.insert(0, Path(configured).expanduser())
    current = Path.cwd().resolve()
    candidates.extend([current, *current.parents])
    for candidate in dict.fromkeys(candidates):
        if (candidate / "server.py").exists() and (candidate / "folder_paths.py").exists():
            return candidate
    return None


def _load_plugin_module():
    comfy_root = _find_comfy_root()
    if comfy_root is not None:
        sys.path.insert(0, str(comfy_root))

    init_path = PROJECT_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "comfyui_studio_suite_registry_check",
        str(init_path),
        submodule_search_locations=[str(PROJECT_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to build import spec for {init_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = _load_plugin_module()
    node_classes = dict(getattr(module, "NODE_CLASS_MAPPINGS", {}))
    display_names = dict(getattr(module, "NODE_DISPLAY_NAME_MAPPINGS", {}))

    missing_display = [key for key in node_classes if key not in display_names]
    missing_required: list[tuple[str, str]] = []
    input_errors: list[tuple[str, str, str]] = []

    for key, cls in node_classes.items():
        for attr in ("INPUT_TYPES", "RETURN_TYPES", "FUNCTION"):
            if not hasattr(cls, attr):
                missing_required.append((key, attr))
        if hasattr(cls, "INPUT_TYPES"):
            try:
                cls.INPUT_TYPES()
            except Exception as exc:
                input_errors.append((key, type(exc).__name__, str(exc)[:300]))

    result = {
        "node_count": len(node_classes),
        "missing_display": missing_display,
        "missing_required": missing_required,
        "input_errors": input_errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if missing_display or missing_required or input_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
