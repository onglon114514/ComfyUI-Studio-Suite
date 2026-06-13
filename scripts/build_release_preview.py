import argparse
import shutil
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUT = PROJECT_DIR.parent / "dist"

EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".ruff_cache",
    ".mypy_cache",
}

EXCLUDE_FILES = {
    "task_agent_config.local.json",
}

GENERATED_RUNTIME_SUFFIXES = {
    ".kcpps",
    ".log",
}

LARGE_RESOURCE_FILES = {
    "danbooru_character_aliases.generated.json",
    "danbooru_character_webui.normalized.jsonl",
    "danbooru_character_webui.xlsx",
    "danbooru_tags_cooccurrence.csv",
    "danbooru_artist_wildcard-D站画师列表.txt",
    "tag count.xlsx",
    "tag_count_tags_统计.json",
    "tag_count_tags_统计.jsonl",
    "tag_count_channels_统计.json",
    "tag_count_channels_统计.jsonl",
}


def should_skip(path, include_large_resources):
    rel = path.relative_to(PROJECT_DIR)
    parts = set(rel.parts)
    if parts & EXCLUDE_DIRS:
        return True
    if path.name in EXCLUDE_FILES:
        return True
    if "runtime" in rel.parts and path.suffix.lower() in GENERATED_RUNTIME_SUFFIXES:
        return True
    if not include_large_resources and rel.parts and rel.parts[0] == "resources" and path.name in LARGE_RESOURCE_FILES:
        return True
    return False


def copy_tree(destination, include_large_resources):
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    for source in PROJECT_DIR.rglob("*"):
        if should_skip(source, include_large_resources):
            continue
        target = destination / source.relative_to(PROJECT_DIR)
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    example_config = destination / "config" / "task_agent_config.example.json"
    local_config = destination / "config" / "task_agent_config.local.json"
    if example_config.exists():
        shutil.copy2(example_config, local_config)

    example_profiles = destination / "config" / "backend_profiles.example.json"
    release_profiles = destination / "config" / "backend_profiles.json"
    if example_profiles.exists():
        shutil.copy2(example_profiles, release_profiles)


def main():
    parser = argparse.ArgumentParser(description="Build a movable preview package for ComfyUI Studio Suite.")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--name", default="comfyui_studio_suite_preview")
    parser.add_argument("--include-large-resources", action="store_true")
    args = parser.parse_args()

    destination = Path(args.out).resolve() / args.name
    copy_tree(destination, include_large_resources=args.include_large_resources)
    print(f"release_preview_dir={destination}")
    print("next:")
    print(f"  1. Copy this folder into ComfyUI/custom_nodes/{args.name}")
    print("  2. Edit config/backend_profiles.json model paths")
    print("  3. Run: python scripts/doctor_release.py")


if __name__ == "__main__":
    main()
