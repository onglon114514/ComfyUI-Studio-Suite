import argparse
from pathlib import Path


SHIM_DIR_NAME = "000_task_agent_llama_preload"


SHIM_TEMPLATE = r'''import os
import sys
from pathlib import Path


def _find_suite_root():
    explicit = os.getenv("TASK_AGENT_SUITE_ROOT", "").strip()
    if explicit:
        path = Path(explicit)
        if (path / "runtime" / "python_libs" / "llama_cpp_python_cu130").exists():
            return path

    hint_file = Path(__file__).resolve().parent / "TASK_AGENT_SUITE_ROOT.txt"
    if hint_file.exists():
        path = Path(hint_file.read_text(encoding="utf-8", errors="replace").strip())
        if (path / "runtime" / "python_libs" / "llama_cpp_python_cu130").exists():
            return path

    custom_nodes_dir = Path(__file__).resolve().parent.parent
    for name in (
        "comfyui_studio_suite_prompt_studio_merge",
        "comfyui_studio_suite",
        "comfyui_studio_suite_preview",
    ):
        candidate = custom_nodes_dir / name
        if (candidate / "runtime" / "python_libs" / "llama_cpp_python_cu130").exists():
            return candidate
    return None


def _preload_private_llama_cpp():
    suite_root = _find_suite_root()
    if suite_root is None:
        print("[TaskAgentPreload] suite root not found; skipping private llama_cpp preload")
        return

    private_root = suite_root / "runtime" / "python_libs" / "llama_cpp_python_cu130"
    lib_dir = private_root / "llama_cpp" / "lib"
    dll_dirs = [lib_dir, private_root / "bin"]
    try:
        import torch

        dll_dirs.append(Path(torch.__file__).resolve().parent / "lib")
    except Exception:
        pass

    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        for dll_dir in dll_dirs:
            if dll_dir.exists():
                os.add_dll_directory(str(dll_dir))

    private_text = str(private_root)
    if private_text not in sys.path:
        sys.path.insert(0, private_text)

    try:
        import llama_cpp
    except Exception as error:
        print(f"[TaskAgentPreload] failed to preload private llama_cpp: {error}")
        return

    print(
        "[TaskAgentPreload] llama_cpp preloaded from "
        f"{getattr(llama_cpp, '__file__', '<unknown>')} "
        f"version={getattr(llama_cpp, '__version__', '<unknown>')}"
    )


_preload_private_llama_cpp()

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
'''


def main():
    parser = argparse.ArgumentParser(description="Install an early ComfyUI custom-node shim that preloads Task Agent private llama_cpp.")
    parser.add_argument("--custom-nodes-dir", required=True)
    parser.add_argument("--suite-root", default="")
    args = parser.parse_args()

    custom_nodes_dir = Path(args.custom_nodes_dir).resolve()
    shim_dir = custom_nodes_dir / SHIM_DIR_NAME
    shim_dir.mkdir(parents=True, exist_ok=True)
    init_path = shim_dir / "__init__.py"
    init_path.write_text(SHIM_TEMPLATE, encoding="utf-8")

    if args.suite_root:
        env_path = shim_dir / "TASK_AGENT_SUITE_ROOT.txt"
        env_path.write_text(str(Path(args.suite_root).resolve()), encoding="utf-8")

    print(f"installed_preload_shim={shim_dir}")
    print("Restart ComfyUI, then check startup logs for [TaskAgentPreload].")


if __name__ == "__main__":
    main()
