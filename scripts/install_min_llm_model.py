import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_DIR / "config" / "model_downloads.json"
BACKEND_PROFILES_PATH = PROJECT_DIR / "config" / "backend_profiles.json"
BACKEND_PROFILES_EXAMPLE_PATH = PROJECT_DIR / "config" / "backend_profiles.example.json"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def hf_resolve_url(repo_id, filename, revision="main"):
    return f"https://huggingface.co/{repo_id}/resolve/{revision}/{filename}"


def print_manual_instructions(item, files):
    repo_id = item["repo_id"]
    revision = item.get("revision", "main")
    print("\n[manual] Automatic download failed or was skipped.")
    print("[manual] Download the files below and place them into the target paths:")
    for file_spec in files:
        target = PROJECT_DIR / file_spec["target"]
        print(f"- {hf_resolve_url(repo_id, file_spec['filename'], revision)}")
        print(f"  -> {target}")
    print("\n[manual] After placing the file, run:")
    print("  python scripts/doctor_release.py")


def download_file(url, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(target.suffix + ".part")
    print(f"[download] {url}")
    print(f"[target]   {target}")
    request = urllib.request.Request(url, headers={"User-Agent": "ComfyUI-Studio-Suite"})
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length", "0") or 0)
        downloaded = 0
        with temp_target.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if total:
                    percent = downloaded * 100 / total
                    print(f"\r[progress] {downloaded // 1024 // 1024} MB / {total // 1024 // 1024} MB ({percent:.1f}%)", end="")
        if total:
            print()
    temp_target.replace(target)


def git_lfs_available():
    git = shutil.which("git")
    if not git:
        return False
    try:
        result = subprocess.run([git, "lfs", "version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def download_with_git_lfs(item, files):
    if not git_lfs_available():
        raise RuntimeError("git-lfs is not available")
    repo_url = f"https://huggingface.co/{item['repo_id']}"
    with tempfile.TemporaryDirectory(prefix="studio_suite_hf_") as temp_dir:
        temp_path = Path(temp_dir) / "repo"
        subprocess.check_call(["git", "lfs", "install"])
        subprocess.check_call(["git", "clone", "--depth", "1", repo_url, str(temp_path)])
        for file_spec in files:
            source = temp_path / file_spec["filename"]
            target = PROJECT_DIR / file_spec["target"]
            if not source.exists():
                raise RuntimeError(f"file not found in repo: {file_spec['filename']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            print(f"[ok] {target}")


def ensure_backend_profiles():
    example = load_json(BACKEND_PROFILES_EXAMPLE_PATH)
    if BACKEND_PROFILES_PATH.exists():
        profiles = load_json(BACKEND_PROFILES_PATH)
    else:
        profiles = {}
    changed = False
    for key in ("gemma4_e2b_hauhau_q8", "gemma4_e2b_hauhau_q8_vision"):
        if key in example and key not in profiles:
            profiles[key] = example[key]
            changed = True
    if changed or not BACKEND_PROFILES_PATH.exists():
        save_json(BACKEND_PROFILES_PATH, profiles or example)
        print(f"[config] updated {BACKEND_PROFILES_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Install the minimum local LLM model for ComfyUI Studio Suite.")
    parser.add_argument("--with-mmproj", action="store_true", help="Also download the optional vision mmproj file.")
    parser.add_argument("--method", choices=["auto", "direct", "git"], default="auto", help="Download method.")
    parser.add_argument("--manual", action="store_true", help="Only print manual download instructions.")
    args = parser.parse_args()

    manifest = load_json(MANIFEST_PATH)
    item = manifest["minimum_text_llm"]
    files = [file_spec for file_spec in item["files"] if file_spec.get("required") or args.with_mmproj]
    ensure_backend_profiles()

    missing = [file_spec for file_spec in files if not (PROJECT_DIR / file_spec["target"]).exists()]
    if not missing:
        print("[ok] minimum LLM files already exist")
        return 0

    if args.manual:
        print_manual_instructions(item, missing)
        return 0

    try:
        if args.method == "git" or (args.method == "auto" and git_lfs_available()):
            download_with_git_lfs(item, missing)
        else:
            repo_id = item["repo_id"]
            revision = item.get("revision", "main")
            for file_spec in missing:
                download_file(hf_resolve_url(repo_id, file_spec["filename"], revision), PROJECT_DIR / file_spec["target"])
    except Exception as error:
        print(f"[error] download failed: {error}")
        print_manual_instructions(item, missing)
        return 2

    ensure_backend_profiles()
    print("[ok] minimum LLM model is installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
