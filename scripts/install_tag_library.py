"""Install or update the public Danbooru tag library from Hugging Face.

The library is a read-only SQLite catalog plus its `manifest.json`. It is far too
large for Git, so it is published as a Hugging Face dataset asset. This script is
the only supported way to fetch it: users either run it, or drop the two files
into `resources/danbooru_library/` by hand.

Guarantees:

* Nothing is published until the downloaded file passed size and SHA-256
  verification, so a failed run can never destroy a working library.
* The manifest is written last; the previous catalog file is kept until the new
  one is in place.
* This script never builds a library. Building stays in `build_tag_library.py`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
LIBRARY_DIR = PACKAGE_DIR / "resources" / "danbooru_library"
MANIFEST_NAME = "manifest.json"

DEFAULT_REPOSITORY = "onglon114514/ComfyUI-Studio-Suite-Resources"
DEFAULT_SUBDIR = "danbooru_library"
DEFAULT_REVISION = "main"

DOWNLOAD_CHUNK = 4 * 1024 * 1024
USER_AGENT = "ComfyUI-Studio-Suite-tag-library-installer/1"


class InstallError(RuntimeError):
    """Any failure that must leave the existing library untouched."""


# --------------------------------------------------------------------------- #
# local state
# --------------------------------------------------------------------------- #
def read_manifest(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def local_state(target: Path | None = None) -> tuple[dict | None, Path | None]:
    directory = (target or LIBRARY_DIR).resolve()
    manifest_path = directory / MANIFEST_NAME
    manifest = read_manifest(manifest_path)
    database_name = str((manifest or {}).get("database") or "").strip()
    if not database_name:
        return manifest, None
    database_path = (directory / database_name).resolve()
    if database_path.parent != directory or not database_path.is_file():
        return manifest, None
    return manifest, database_path


def catalog_is_readable(path: Path) -> bool:
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=5)
    except sqlite3.Error:
        return False
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return True
    except sqlite3.Error:
        return False
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# sources
# --------------------------------------------------------------------------- #
def resolve_source(value: str) -> tuple[str, str]:
    """Return ("dir"|"http"|"hf", resolved value)."""
    if not value:
        return "hf", DEFAULT_REPOSITORY
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        return "dir", str(candidate.resolve())
    if value.startswith("http://") or value.startswith("https://"):
        return "http", value.rstrip("/")
    if "/" not in value:
        raise InstallError(f"--source 既不是目录也不是 http(s) 地址，也不是 Hugging Face 仓库 id：{value}")
    return "hf", value.strip()


def _request(url: str, *, head: bool = False, headers: dict | None = None, timeout: int = 60):
    request = urllib.request.Request(url, method="HEAD" if head else "GET")
    request.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    return urllib.request.urlopen(request, timeout=timeout)


class Source:
    """Uniform access to the manifest and catalog of one published library."""

    def __init__(self, kind: str, value: str, subdir: str, revision: str):
        self.kind = kind
        self.value = value
        self.subdir = subdir.strip("/") or DEFAULT_SUBDIR
        self.revision = revision

    # -- description -------------------------------------------------------- #
    def describe(self) -> str:
        if self.kind == "dir":
            return f"本地目录 {self.value}"
        if self.kind == "http":
            return f"HTTP {self.value}"
        return f"Hugging Face {self.value}@{self.revision}"

    def _join(self, *parts: str) -> str:
        cleaned = [part.strip("/") for part in parts if part and part.strip("/")]
        return "/".join(cleaned)

    # -- remote metadata ---------------------------------------------------- #
    def remote_entries(self) -> dict[str, dict]:
        """Map of file name -> {"size": int|None, "sha256": str|None}."""
        if self.kind == "dir":
            directory = Path(self.value)
            entries: dict[str, dict] = {}
            for child in sorted(directory.iterdir()):
                if child.is_file():
                    entries[child.name] = {"size": child.stat().st_size, "sha256": None}
            return entries
        if self.kind == "http":
            entries = {MANIFEST_NAME: {"size": None, "sha256": None}}
            listing_url = f"{self.value}/resource_files.json"
            try:
                with _request(listing_url) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                for item in payload.get("files", []):
                    name = str(item.get("name") or "").strip("/")
                    if name:
                        entries[name] = {
                            "size": int(item["size"]) if str(item.get("size", "")).isdigit() else None,
                            "sha256": str(item.get("sha256") or "") or None,
                        }
            except (urllib.error.URLError, ValueError, TimeoutError, OSError):
                pass
            return entries
        # huggingface
        url = (
            "https://huggingface.co/api/datasets/"
            f"{urllib.parse.quote(self.value, safe='/')}/tree/{urllib.parse.quote(self.revision, safe='')}"
            f"/{urllib.parse.quote(self.subdir, safe='/')}?recursive=true"
        )
        with _request(url) as response:
            payload = json.loads(response.read().decode("utf-8"))
        entries = {}
        for item in payload if isinstance(payload, list) else []:
            if str(item.get("type")) != "file":
                continue
            name = str(item.get("path") or "").split("/")[-1]
            lfs = item.get("lfs") or {}
            entries[name] = {
                "size": int(lfs.get("size") or item.get("size") or 0) or None,
                "sha256": str(lfs.get("oid") or "") or None,
            }
        return entries

    def remote_manifest(self) -> dict:
        name = MANIFEST_NAME
        if name not in self.remote_entries():
            raise InstallError(
                f"{self.describe()} 上没有找到 {self.subdir}/{MANIFEST_NAME}；"
                "请确认仓库里已经发布了词典资源，或者用 --source 指向正确的离线目录。"
            )
        with self.open_binary(name) as response:
            try:
                payload = json.loads(response.read().decode("utf-8"))
            except ValueError as error:
                raise InstallError(f"{MANIFEST_NAME} 不是合法 JSON：{error}") from error
        if not isinstance(payload, dict) or not str(payload.get("database") or "").strip():
            raise InstallError(f"{MANIFEST_NAME} 缺少 database 字段")
        return payload

    # -- bytes -------------------------------------------------------------- #
    def open_binary(self, name: str, *, offset: int = 0):
        headers = {"Range": f"bytes={offset}-"} if offset else None
        if self.kind == "dir":
            path = Path(self.value) / name
            if not path.is_file():
                raise InstallError(f"离线目录缺少文件：{path}")
            return path.open("rb")
        if self.kind == "http":
            url = f"{self.value}/{urllib.parse.quote(name)}"
        else:
            url = (
                "https://huggingface.co/datasets/"
                f"{urllib.parse.quote(self.value, safe='/')}/resolve/"
                f"{urllib.parse.quote(self.revision, safe='')}/"
                f"{urllib.parse.quote(self._join(self.subdir, name), safe='/')}"
            )
        try:
            return _request(url, headers=headers, timeout=120)
        except urllib.error.HTTPError as error:
            raise InstallError(f"下载 {name} 失败：HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise InstallError(
                f"下载 {name} 失败：{error}。"
                "请检查网络，必要时用 --source 指向镜像或离线目录。"
            ) from error


# --------------------------------------------------------------------------- #
# download + verify
# --------------------------------------------------------------------------- #
def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(DOWNLOAD_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download_to(source: Source, name: str, part_path: Path, expected_size: int | None) -> None:
    offset = part_path.stat().st_size if part_path.is_file() else 0
    if expected_size and offset > expected_size:
        offset = 0
        part_path.unlink(missing_ok=True)
    if offset and expected_size and offset == expected_size:
        return
    handle = source.open_binary(name, offset=offset if offset else 0)
    try:
        mode = "ab" if offset else "wb"
        with part_path.open(mode) as target:
            while True:
                block = handle.read(DOWNLOAD_CHUNK)
                if not block:
                    break
                target.write(block)
    finally:
        handle.close()


def install(args) -> int:
    kind, value = resolve_source(args.source)
    source = Source(kind, value, args.subdir, args.revision)
    target = Path(args.target).expanduser().resolve()
    local_manifest, local_database = local_state(target)
    local_version = str((local_manifest or {}).get("version") or "")

    if local_database is None:
        print(f"[info] 本机还没有可用的词典库（{target}）")
    else:
        print(f"[info] 本机词典库版本 {local_version or '(未知)'} -> {local_database.name}")

    if not args.check:
        target.mkdir(parents=True, exist_ok=True)
    entries = source.remote_entries()
    remote_manifest = source.remote_manifest()
    remote_version = str(remote_manifest.get("version") or "")
    remote_database = str(remote_manifest["database"]).strip()
    if Path(remote_database).name != remote_database or not remote_database.startswith("catalog-"):
        raise InstallError(f"远端 manifest 的 database 字段不合法：{remote_database}")

    print(f"[info] 远端来源 {source.describe()}")
    print(f"[info] 远端词典库版本 {remote_version or '(未知)'} -> {remote_database}")

    if local_database is not None and local_database.name == remote_database and not args.force:
        counts = (remote_manifest.get("counts") or {}).get("tag_count")
        print(f"[ok] 已是最新版本，无需下载。词条数 {counts if counts is not None else '未知'}")
        return 0

    expected = entries.get(remote_database) or {}
    expected_size = expected.get("size")
    expected_sha = expected.get("sha256")
    if expected_size:
        print(f"[info] 需要下载 {remote_database}（{expected_size / 1048576:.1f} MB）")

    if args.check:
        print("[ok] --check：有可用更新，重新运行本脚本（去掉 --check）即可下载")
        return 0

    part_path = target / f".{remote_database}.part"
    try:
        download_to(source, remote_database, part_path, expected_size)
    except InstallError as error:
        print(f"[error] {error}")
        print("[info] 已保留现有词典库，未做任何替换")
        return 2

    actual_size = part_path.stat().st_size
    if expected_size and actual_size != expected_size:
        print(f"[error] 大小不符：期望 {expected_size}，实际 {actual_size}")
        print("[info] 已保留现有词典库，未做任何替换")
        return 3

    print("[info] 正在校验 SHA-256 …")
    actual_sha = sha256_of(part_path)
    if expected_sha and actual_sha != expected_sha:
        print(f"[error] SHA-256 不符：期望 {expected_sha}，实际 {actual_sha}")
        print("[info] 已保留现有词典库，未做任何替换")
        return 4
    if not expected_sha:
        print("[warn] 来源未提供 SHA-256，仅按大小与可读性校验")

    if not catalog_is_readable(part_path):
        print("[error] 下载的文件不是可读的 SQLite 词典库")
        print("[info] 已保留现有词典库，未做任何替换")
        return 5

    destination = target / remote_database
    os.replace(part_path, destination)
    manifest_temp = target / f".{MANIFEST_NAME}.tmp"
    manifest_temp.write_text(
        json.dumps(remote_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(manifest_temp, target / MANIFEST_NAME)
    print(f"[ok] 词典库已安装：{destination}")
    print("[info] 旧版本文件已保留在 resources/danbooru_library/，确认新库可用后可自行清理")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从 Hugging Face（或离线目录）安装/更新公共 Danbooru 词典库",
    )
    parser.add_argument("--source", default="", help="HF 仓库 id、http(s) 地址或本地目录")
    parser.add_argument("--subdir", default=DEFAULT_SUBDIR, help="仓库内的资源子目录")
    parser.add_argument("--revision", default=DEFAULT_REVISION, help="HF revision/branch")
    parser.add_argument("--target", default=str(LIBRARY_DIR), help="安装目录，默认 resources/danbooru_library")
    parser.add_argument("--check", action="store_true", help="只报告是否有更新，不下载")
    parser.add_argument("--force", action="store_true", help="即使版本相同也重新下载校验")
    args = parser.parse_args(argv)
    try:
        return install(args)
    except InstallError as error:
        print(f"[error] {error}")
        return 2
    except KeyboardInterrupt:
        print("[info] 已取消，现有词典库未被改动")
        return 130


if __name__ == "__main__":
    sys.exit(main())
