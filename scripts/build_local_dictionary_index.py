from __future__ import annotations

import importlib.util
import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_DIR / "prompt_studio" / "local_dictionary.py"
SPEC = importlib.util.spec_from_file_location("studio_suite_local_dictionary", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load local dictionary module: {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
LocalDanbooruDictionary = MODULE.LocalDanbooruDictionary


def main() -> int:
    dictionary = LocalDanbooruDictionary(PROJECT_DIR, PROJECT_DIR / "prompt_studio" / "storage")
    print(json.dumps(dictionary.status(), ensure_ascii=False, indent=2))
    result = dictionary.build_index()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
