"""Offline regression suite for scripts/install_tag_library.py.

Run: python -B scripts/test_install_tag_library.py
All installs go to throwaway --target directories. Local fixtures and a local
http.server stand in for Hugging Face, so no network and no real
resources/danbooru_library/ content is touched.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import importlib.util
import io
import json
import shutil
import sqlite3
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REAL_LIBRARY = ROOT / "resources" / "danbooru_library"


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load("test_install_tag_library_target", "scripts/install_tag_library.py")


def make_catalog(path, version, tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("CREATE TABLE tags (canonical TEXT PRIMARY KEY, count INTEGER)")
        connection.execute("INSERT INTO meta (key, value) VALUES (?, ?)", ("version", version))
        connection.executemany(
            "INSERT INTO tags (canonical, count) VALUES (?, ?)",
            [(f"fixture_tag_{index}", index) for index in range(tags)],
        )
        connection.commit()
    finally:
        connection.close()


def write_manifest(directory, version, database, tags):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": version,
                "database": database,
                "counts": {"tag_count": tags},
                "sources": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_resource_files(directory, database, *, size=None, sha256=None):
    """HTTP mirrors may publish resource_files.json with per-file size/sha256."""
    catalog = directory / database
    payload = {
        "files": [
            {"name": "manifest.json"},
            {
                "name": database,
                "size": catalog.stat().st_size if size is None else size,
                "sha256": hashlib.sha256(catalog.read_bytes()).hexdigest() if sha256 is None else sha256,
            },
        ]
    }
    (directory / "resource_files.json").write_text(json.dumps(payload), encoding="utf-8")


def run(argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = installer.main(argv)
    return code, buffer.getvalue()


def target_version(target):
    manifest_path = target / "manifest.json"
    if not manifest_path.is_file():
        return None
    return json.loads(manifest_path.read_text(encoding="utf-8")).get("version")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class InstallTagLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real_names = sorted(path.name for path in REAL_LIBRARY.iterdir()) if REAL_LIBRARY.exists() else None
        manifest_path = REAL_LIBRARY / "manifest.json"
        cls.real_manifest = manifest_path.read_bytes() if manifest_path.is_file() else None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="install-tag-library-test-")
        self.root = Path(self.temp.name)
        self.fixtures = self.root / "fixtures"
        self.target = self.root / "target"
        self.servers = []
        # 夹具 a/b：完整可装的两个版本；夹具 c：manifest 指向缺失的 catalog
        make_catalog(self.fixtures / "a" / ("catalog-" + "a" * 32 + ".sqlite3"), "aaaa", 5)
        write_manifest(self.fixtures / "a", "aaaa", "catalog-" + "a" * 32 + ".sqlite3", 5)
        make_catalog(self.fixtures / "b" / ("catalog-" + "b" * 32 + ".sqlite3"), "bbbb", 9)
        write_manifest(self.fixtures / "b", "bbbb", "catalog-" + "b" * 32 + ".sqlite3", 9)
        write_manifest(self.fixtures / "c", "cccc", "catalog-" + "c" * 32 + ".sqlite3", 7)

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.temp.cleanup()

    def serve(self, directory):
        handler = functools.partial(QuietHandler, directory=str(directory))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def install_a(self):
        code, output = run(["--source", str(self.fixtures / "a"), "--target", str(self.target)])
        self.assertEqual(code, 0, output)
        return output

    def test_fresh_install_success(self):
        output = self.install_a()
        self.assertEqual(target_version(self.target), "aaaa")
        catalog = self.target / ("catalog-" + "a" * 32 + ".sqlite3")
        self.assertTrue(catalog.is_file())
        self.assertTrue(installer.catalog_is_readable(catalog))
        self.assertNotIn(".part", " ".join(path.name for path in self.target.iterdir()))
        self.assertIn("词典库已安装", output)

    def test_same_version_is_skipped(self):
        self.install_a()
        code, output = run(["--source", str(self.fixtures / "a"), "--target", str(self.target)])
        self.assertEqual(code, 0, output)
        self.assertIn("已是最新", output)
        self.assertEqual(target_version(self.target), "aaaa")

    def test_check_only_reports_without_downloading(self):
        self.install_a()
        code, output = run(["--source", str(self.fixtures / "b"), "--target", str(self.target), "--check"])
        self.assertEqual(code, 0, output)
        self.assertIn("有可用更新", output)
        self.assertEqual(target_version(self.target), "aaaa")
        self.assertFalse((self.target / ("catalog-" + "b" * 32 + ".sqlite3")).exists())

    def test_upgrade_keeps_old_catalog(self):
        self.install_a()
        code, output = run(["--source", str(self.fixtures / "b"), "--target", str(self.target)])
        self.assertEqual(code, 0, output)
        self.assertEqual(target_version(self.target), "bbbb")
        self.assertTrue((self.target / ("catalog-" + "a" * 32 + ".sqlite3")).is_file())
        self.assertTrue((self.target / ("catalog-" + "b" * 32 + ".sqlite3")).is_file())
        self.assertIn("旧版本文件已保留", output)

    def test_missing_catalog_fails_and_keeps_library(self):
        self.install_a()
        names_before = sorted(path.name for path in self.target.iterdir())
        code, output = run(["--source", str(self.fixtures / "c"), "--target", str(self.target)])
        self.assertNotEqual(code, 0, output)
        self.assertEqual(target_version(self.target), "aaaa")
        self.assertIn("已保留现有词典库", output)
        self.assertEqual(sorted(path.name for path in self.target.iterdir()), names_before)

    def test_http_source_installs(self):
        database = "catalog-" + "d" * 32 + ".sqlite3"
        make_catalog(self.fixtures / "d" / database, "dddd", 3)
        write_manifest(self.fixtures / "d", "dddd", database, 3)
        write_resource_files(self.fixtures / "d", database)
        base_url = self.serve(self.fixtures / "d")
        code, output = run(["--source", base_url, "--target", str(self.target)])
        self.assertEqual(code, 0, output)
        self.assertEqual(target_version(self.target), "dddd")
        self.assertTrue((self.target / database).is_file())
        self.assertNotIn("未提供 SHA-256", output)

    def test_http_hash_mismatch_fails_and_keeps_library(self):
        self.install_a()
        database = "catalog-" + "e" * 32 + ".sqlite3"
        make_catalog(self.fixtures / "e" / database, "eeee", 4)
        write_manifest(self.fixtures / "e", "eeee", database, 4)
        write_resource_files(self.fixtures / "e", database, sha256="0" * 64)
        base_url = self.serve(self.fixtures / "e")
        code, output = run(["--source", base_url, "--target", str(self.target)])
        self.assertEqual(code, 4, output)
        self.assertIn("SHA-256 不符", output)
        self.assertEqual(target_version(self.target), "aaaa")
        self.assertTrue((self.target / ("catalog-" + "a" * 32 + ".sqlite3")).is_file())
        self.assertFalse((self.target / database).exists())

    def test_zz_real_library_untouched(self):
        if self.real_names is None:
            self.assertFalse(REAL_LIBRARY.exists())
            return
        self.assertEqual(sorted(path.name for path in REAL_LIBRARY.iterdir()), self.real_names)
        manifest_path = REAL_LIBRARY / "manifest.json"
        if self.real_manifest is None:
            self.assertFalse(manifest_path.exists())
        else:
            self.assertEqual(manifest_path.read_bytes(), self.real_manifest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
