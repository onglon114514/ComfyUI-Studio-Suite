"""Publish a new immutable dictionary version with refreshed semantic groups.

This copies the active SQLite catalog and never rebuilds tags from partial raw
sources. The user's quick-tag YAML remains unchanged and is only used as a
bounded, provenance-labelled navigation seed.
"""
from __future__ import annotations

import argparse
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
_spec = importlib.util.spec_from_file_location("_tag_library_builder", ROOT / "scripts/build_tag_library.py")
_builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_builder)


def augment(source_package: Path, output_dir: Path) -> dict:
    source_package = Path(source_package).resolve()
    output_dir = Path(output_dir).resolve()
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    active_name = str(manifest.get("database", ""))
    if not re.fullmatch(r"catalog-[a-f0-9]{32}\.sqlite3", active_name):
        raise ValueError("active manifest has an invalid database pointer")
    active_database = (output_dir / active_name).resolve()
    if active_database.parent != output_dir or not active_database.is_file():
        raise ValueError("active dictionary database is missing")

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
            db.create_function("normalize", 1, _builder.normalize, deterministic=True)
            db.execute("DELETE FROM tag_sources WHERE source='quick_group_semantics'")
            db.execute(
                "DELETE FROM tag_groups WHERE group_id IN "
                "(SELECT id FROM groups WHERE source='studio_suite_proposal')"
            )
            importer = _builder._Importer(db)
            quick_path = source_package / "prompt_studio/storage/group_tags/zh_CN.yaml"
            quick_source = _builder._import_quick_group_semantics(importer, quick_path, source_package)
            _builder._finish_groups(db)
            db.execute("UPDATE metadata SET value=? WHERE key='version'", (version,))
            counts = _builder._counts(db)
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("augmented dictionary failed quick_check")
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
        sources = [source for source in manifest.get("sources", []) if source.get("id") != "quick_group_semantics"]
        sources.append(quick_source)
        updated["sources"] = sources
        coverage = dict(manifest.get("coverage", {}))
        coverage["semantic_navigation"] = (
            "selected built-in quick groups; existing canonical tags only; "
            "not an official Danbooru semantic taxonomy"
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-package", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "resources/danbooru_library")
    args = parser.parse_args()
    try:
        result = augment(args.source_package, args.output_dir)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        parser.exit(1, f"Semantic augmentation failed; active manifest preserved: {exc}\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
