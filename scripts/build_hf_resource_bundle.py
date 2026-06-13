import argparse
import json
import shutil
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
RESOURCES_DIR = PROJECT_DIR / "resources"
DIST_DIR = PROJECT_DIR.parent / "dist"
DEFAULT_NAME = "comfyui_studio_suite_hf_resources"


RESOURCE_FILES = [
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
    "Danbooru服装查询资源_本地版_2026-05-26.jsonl",
    "Danbooru服装查询资源_层级版_2026-05-26.txt",
    "服装生成模板_system_legacy.md",
    "danbooru_character_webui.columns.json",
]


README_TEXT = """---
license: other
language:
- zh
- en
tags:
- comfyui
- anime
- danbooru
- prompt-engineering
- captioning
size_categories:
- 100M<n<1G
---

# ComfyUI Studio Suite Resources

This dataset repository hosts optional large resource files for the
`ComfyUI-Studio-Suite` project.

Main code repository:

- https://github.com/onglon114514/ComfyUI-Studio-Suite

These files are intentionally kept out of the GitHub code repository because
they are large, generated, or better distributed through Git LFS.

## Included Resource Groups

- Danbooru character alias dictionaries
- Danbooru character source tables
- Danbooru tag co-occurrence statistics
- Danbooru tag count statistics
- artist wildcard lists
- clothing lookup resources

## How To Use

Download the files you need and place them into the `resources/` directory of
the ComfyUI Studio Suite node package with the same filenames.

The node package will detect matching files automatically through its built-in
resource catalog.

## Notes

- This repository is for resource distribution only.
- Model files are not included here.
- Review any licensing or redistribution constraints of source datasets before
  broader public redistribution.
"""


GITATTRIBUTES_TEXT = """*.json filter=lfs diff=lfs merge=lfs -text
*.jsonl filter=lfs diff=lfs merge=lfs -text
*.csv filter=lfs diff=lfs merge=lfs -text
*.xlsx filter=lfs diff=lfs merge=lfs -text
*.txt filter=lfs diff=lfs merge=lfs -text
*.md text eol=lf
"""


def build_manifest():
    manifest_path = PROJECT_DIR / "config" / "resource_manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {}


def copy_resources(destination: Path):
    copied = []
    missing = []
    for filename in RESOURCE_FILES:
        source = RESOURCES_DIR / filename
        target = destination / filename
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(
                {
                    "filename": filename,
                    "bytes": source.stat().st_size,
                }
            )
        else:
            missing.append(filename)
    return copied, missing


def main():
    parser = argparse.ArgumentParser(description="Build a Hugging Face resource bundle directory.")
    parser.add_argument("--out", default=str(DIST_DIR))
    parser.add_argument("--name", default=DEFAULT_NAME)
    args = parser.parse_args()

    destination = Path(args.out).resolve() / args.name
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    copied, missing = copy_resources(destination)

    (destination / "README.md").write_text(README_TEXT, encoding="utf-8")
    (destination / ".gitattributes").write_text(GITATTRIBUTES_TEXT, encoding="utf-8")

    manifest = {
        "source_project": "ComfyUI-Studio-Suite",
        "source_repo": "https://github.com/onglon114514/ComfyUI-Studio-Suite",
        "copied_files": copied,
        "missing_files": missing,
        "resource_manifest": build_manifest(),
    }
    (destination / "resource_bundle_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    total_bytes = sum(item["bytes"] for item in copied)
    print(f"hf_resource_bundle_dir={destination}")
    print(f"copied_files={len(copied)}")
    print(f"missing_files={len(missing)}")
    print(f"total_bytes={total_bytes}")


if __name__ == "__main__":
    main()
