"""Publish a richer immutable tag catalog from a verified Danbooru snapshot.

The source file is never bundled into the node. The active SQLite database is
copied, augmented, validated, then selected through an atomic manifest update.
Old databases and the user's quick-tag YAML are not modified.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHA256 = "b44b916e8c85a5dd77f9271dbafeb33ce40c5f0a90b8b5a7ed34a724548f4388"
FOOTWEAR_PATTERN = re.compile(
    r"(?:^|_)(?:footwear|shoes?|boots?|sandals?|slippers?|sneakers?|loafers?|"
    r"pumps|moccasins?|clogs?|geta|zouri|uwabaki|waraji|espadrilles?|"
    r"mary_janes)(?:_|$)"
)

_spec = importlib.util.spec_from_file_location("_tag_library_builder", ROOT / "scripts/build_tag_library.py")
_builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_builder)


EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS works(
 name TEXT PRIMARY KEY,term TEXT NOT NULL,label_zh TEXT NOT NULL DEFAULT '',
 post_count INTEGER,character_count INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS tag_works(
 work TEXT NOT NULL REFERENCES works(name),canonical TEXT NOT NULL REFERENCES tags(canonical),
 PRIMARY KEY(work,canonical)
) WITHOUT ROWID;
"""


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _active(output_dir: Path):
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    name = str(manifest.get("database", ""))
    if not re.fullmatch(r"catalog-[a-f0-9]{32}\.sqlite3", name):
        raise ValueError("active manifest has an invalid database pointer")
    database = (output_dir / name).resolve()
    if database.parent != output_dir or not database.is_file():
        raise ValueError("active dictionary database is missing")
    return manifest_path, manifest, name, database


def augment(snapshot: Path, output_dir: Path, expected_sha256: str = DEFAULT_SHA256,
            revision: str = "a860558") -> dict:
    snapshot, output_dir = Path(snapshot).resolve(), Path(output_dir).resolve()
    if not snapshot.is_file():
        raise ValueError("snapshot file does not exist")
    actual_sha256 = _digest(snapshot)
    if expected_sha256 and actual_sha256.casefold() != expected_sha256.casefold():
        raise ValueError(f"snapshot SHA256 mismatch: {actual_sha256}")
    manifest_path, manifest, active_name, active_database = _active(output_dir)

    lock = output_dir / "augment.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("augment.lock exists; another catalog update may be running") from exc
    identity = os.fstat(descriptor)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump({"pid": os.getpid(), "created_at": datetime.now(timezone.utc).isoformat()}, stream)

    started = time.monotonic()
    version = uuid.uuid4().hex
    database = output_dir / f"catalog-{version}.sqlite3"
    temporary = output_dir / f".{database.name}.tmp"
    pointer_tmp = output_dir / f".manifest-{version}.tmp"
    published = False
    try:
        shutil.copyfile(active_database, temporary)
        db = sqlite3.connect(temporary)
        try:
            quoted_output = str(output_dir).replace("'", "''")
            db.execute(f"PRAGMA temp_store_directory='{quoted_output}'")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA temp_store=FILE")
            db.execute("PRAGMA cache_size=-32768")
            db.execute("PRAGMA synchronous=NORMAL")
            db.create_function("normalize", 1, _builder.normalize, deterministic=True)
            db.executescript(EXTRA_SCHEMA)
            source = {
                "id": "danbooru_snapshot_qdlabs",
                "path": "qdlabs/danbooru-tags@a860558/tags.jsonl",
                "location": "external_verified_cache",
                "size": snapshot.stat().st_size,
                "mtime": snapshot.stat().st_mtime,
                "mtime_ns": snapshot.stat().st_mtime_ns,
                "sha256": None,
                "imported": 0,
                "rejected": 0,
                "status": "pending",
                "snapshot_verified": True,
                "revision": revision,
                "license": "Apache-2.0",
                "upstream": "https://huggingface.co/datasets/qdlabs/danbooru-tags",
            }
            importer = _builder._Importer(db)
            with _builder._lines(snapshot, source) as lines:
                importer.danbooru_snapshot(lines, source)
            source["unique_tags"] = db.execute(
                "SELECT count(*) FROM tag_sources WHERE source=?", (source["id"],)
            ).fetchone()[0]

            db.execute(
                "INSERT INTO groups(id,label_zh,parent_id,source,review_status) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET label_zh=excluded.label_zh,parent_id=excluded.parent_id," 
                "source=excluded.source,review_status=excluded.review_status",
                ("studio:footwear_candidates", "鞋类相关候选", "group:attire",
                 source["id"], "heuristic_unreviewed"),
            )
            db.execute("DELETE FROM tag_groups WHERE group_id='studio:footwear_candidates'")
            candidates = [row[0] for row in db.execute(
                "SELECT t.canonical FROM tags t JOIN tag_sources s ON s.canonical=t.canonical "
                "WHERE s.source=? AND t.kind='general' ORDER BY t.canonical",
                (source["id"],),
            ) if FOOTWEAR_PATTERN.search(row[0])]
            db.executemany(
                "INSERT OR IGNORE INTO tag_groups(group_id,canonical) VALUES('studio:footwear_candidates',?)",
                ((canonical,) for canonical in candidates),
            )
            source["footwear_candidates"] = len(candidates)

            _builder._finish_works(db)
            _builder._finish_groups(db)
            db.executescript("""
                CREATE INDEX IF NOT EXISTS tags_kind_canonical ON tags(kind,canonical);
                CREATE INDEX IF NOT EXISTS tags_count_canonical ON tags(count DESC,canonical);
                CREATE INDEX IF NOT EXISTS terms_canonical_role ON terms(canonical,role,text);
                CREATE INDEX IF NOT EXISTS tag_groups_canonical ON tag_groups(canonical,group_id);
                CREATE INDEX IF NOT EXISTS works_term_name ON works(term,name);
                CREATE INDEX IF NOT EXISTS tag_works_canonical_work ON tag_works(canonical,work);
            """)
            db.execute("UPDATE metadata SET value=? WHERE key='version'", (version,))
            counts = _builder._counts(db)
            if counts["tag_count"] < int(manifest.get("counts", {}).get("tag_count", 0)):
                raise ValueError("catalog update would reduce tag coverage")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("augmented dictionary failed quick_check")
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("augmented dictionary failed foreign_key_check")
            db.execute("ANALYZE")
            db.commit()
        finally:
            db.close()

        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        updated = dict(manifest)
        updated.update(
            version=version,
            database=database.name,
            built_at=datetime.now(timezone.utc).isoformat(),
            build_seconds=round(time.monotonic() - started, 3),
            counts=counts,
            database_size=temporary.stat().st_size,
            derived_from={"version": manifest.get("version"), "database": active_name},
        )
        sources = [item for item in manifest.get("sources", []) if item.get("id") != source["id"]]
        sources.append(source)
        updated["sources"] = sources
        coverage = dict(manifest.get("coverage", {}))
        coverage.update(
            official_category=(
                "Danbooru category values from qdlabs filtered snapshot; not a complete official export"
            ),
            catalog_snapshot=(
                "194431 non-deprecated, non-empty tags with wiki pages; third-party API snapshot"
            ),
            work_navigation=(
                "copyright tags plus source-provided character copyright metadata"
            ),
            footwear_candidates=(
                "name-boundary heuristic; explicitly unreviewed and separate from curated shoes"
            ),
        )
        updated["coverage"] = coverage

        os.replace(temporary, database)
        with pointer_tmp.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(updated, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pointer_tmp, manifest_path)
        published = True
        return updated
    finally:
        if not published:
            for path in (temporary, pointer_tmp, database):
                if path.exists():
                    path.unlink()
        if lock.exists():
            current = lock.stat()
            if (identity.st_dev, identity.st_ino) == (current.st_dev, current.st_ino):
                lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "resources/danbooru_library")
    parser.add_argument("--expected-sha256", default=DEFAULT_SHA256)
    parser.add_argument("--revision", default="a860558")
    args = parser.parse_args()
    try:
        result = augment(args.snapshot, args.output_dir, args.expected_sha256, args.revision)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        parser.exit(1, f"Catalog augmentation failed; active manifest preserved: {exc}\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
