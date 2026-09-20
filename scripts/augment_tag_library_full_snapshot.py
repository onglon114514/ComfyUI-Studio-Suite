"""Build an immutable Prompt Studio catalog from a full Danbooru API snapshot.

This is a maintainer tool. PyArrow is only needed while building the SQLite
artifact; Prompt Studio itself keeps a dependency-free, read-only runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

import augment_tag_library_catalog as catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "danbooru_full_snapshot_baton4ik"
REVISION = "e81b8d2e62fc46dfe70ebbb0223420d6bb94cafe"
UPSTREAM = "https://huggingface.co/datasets/baton4ik/danbooru-tag-metadata-snapshot"
EXPECTED = {
    "metadata.json": ("40e25aeeb2561d04caeb15950915ede7557785a0c956d4211b99b2dbcf9dc738", 674),
    "tags.parquet": ("b7aa7d300126dc76c08c24951f82c0dd507f72be5545ae6f666f4b42f61f7126", 1711015),
    "tag_aliases.parquet": ("650387c6a70de86f33843f85fdd65d70963cc1bf81804bd830127adfe0c6b548", 59259),
    "tag_implications.parquet": ("416b2193a2c3effef53f76995a065e3e61636a40a88d3780b6f908f754c097a7", 49334),
}
CATEGORIES = catalog._builder.DANBOORU_CATEGORIES
RELATION_FOOTWEAR_ROOTS = {"shoes", "boots", "sandals", "slippers", "high_heels"}
CANDIDATE_MIN_POST_COUNT = 10

FULL_SCHEMA = """
CREATE TABLE IF NOT EXISTS danbooru_tags(
 canonical TEXT PRIMARY KEY REFERENCES tags(canonical),
 danbooru_id INTEGER NOT NULL,
 category INTEGER NOT NULL,
 post_count INTEGER NOT NULL,
 source TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS danbooru_aliases(
 antecedent TEXT PRIMARY KEY,
 consequent TEXT NOT NULL REFERENCES tags(canonical),
 danbooru_id INTEGER NOT NULL,
 source TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS danbooru_implications(
 antecedent TEXT NOT NULL REFERENCES tags(canonical),
 consequent TEXT NOT NULL REFERENCES tags(canonical),
 danbooru_id INTEGER NOT NULL,
 source TEXT NOT NULL,
 PRIMARY KEY(antecedent,consequent)
) WITHOUT ROWID;
"""


def _verify(source_dir: Path) -> tuple[dict, dict]:
    source_dir = source_dir.resolve()
    metadata_path = source_dir / "metadata.json"
    if not metadata_path.is_file():
        raise ValueError("metadata.json is missing")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    files = {}
    for name, (expected_sha, expected_rows) in EXPECTED.items():
        path = source_dir / name
        if not path.is_file():
            raise ValueError(f"{name} is missing")
        actual_sha = catalog._digest(path)
        if actual_sha != expected_sha:
            raise ValueError(f"{name} SHA256 mismatch: {actual_sha}")
        if name.endswith(".parquet"):
            rows = pq.ParquetFile(path).metadata.num_rows
            if rows != expected_rows:
                raise ValueError(f"{name} row count mismatch: {rows}")
        files[name] = {
            "size": path.stat().st_size,
            "sha256": actual_sha,
            "rows": expected_rows if name.endswith(".parquet") else None,
        }
    for key, expected in {
        "row_count_tags": EXPECTED["tags.parquet"][1],
        "row_count_tag_aliases": EXPECTED["tag_aliases.parquet"][1],
        "row_count_tag_implications": EXPECTED["tag_implications.parquet"][1],
    }.items():
        if metadata.get(key) != expected:
            raise ValueError(f"metadata count mismatch for {key}")
    return metadata, files


def _batches(path: Path, columns: list[str], batch_size: int = 25000):
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=batch_size, columns=columns):
        yield batch.to_pylist()


def _rows(path: Path, columns: list[str], batch_size: int = 25000):
    for batch in _batches(path, columns, batch_size):
        yield from batch


def _relation_descendants(edges: list[tuple[str, str]], roots: set[str]) -> set[str]:
    reverse: dict[str, set[str]] = {}
    for antecedent, consequent in edges:
        reverse.setdefault(consequent, set()).add(antecedent)
    found: set[str] = set()
    stack = list(roots)
    while stack:
        parent = stack.pop()
        for child in reverse.get(parent, ()):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found | roots


def _import_tags(db: sqlite3.Connection, path: Path) -> dict:
    imported = rejected = deprecated = 0
    for batch in _batches(
        path, ["id", "name", "category", "post_count", "is_deprecated"]
    ):
        tag_rows = []
        snapshot_rows = []
        term_rows = []
        for source_rows in batch:
            canonical = catalog._builder._text(source_rows.get("name"))
            category = source_rows.get("category")
            raw_count = source_rows.get("post_count")
            count = (
                raw_count
                if isinstance(raw_count, int)
                and not isinstance(raw_count, bool)
                and 0 <= raw_count <= 9223372036854775807
                else None
            )
            danbooru_id = source_rows.get("id")
            if (
                not canonical
                or isinstance(category, bool)
                or category not in CATEGORIES
                or count is None
                or isinstance(danbooru_id, bool)
                or not isinstance(danbooru_id, int)
            ):
                rejected += 1
                continue
            if source_rows.get("is_deprecated") == 1:
                deprecated += 1
                continue
            kind = CATEGORIES[category]
            tag_rows.append(
                (
                    canonical, "", kind, count, SOURCE_ID, "danbooru_api_snapshot",
                    "", "", 0, 150,
                )
            )
            snapshot_rows.append((canonical, danbooru_id, category, count, SOURCE_ID))
            term_rows.append(
                (catalog._builder.normalize(canonical), canonical, canonical, SOURCE_ID, "canonical")
            )
        db.executemany(catalog._builder.TAG_UPSERT, tag_rows)
        db.executemany(
            "INSERT OR REPLACE INTO danbooru_tags VALUES(?,?,?,?,?)", snapshot_rows
        )
        db.executemany(
            "INSERT OR IGNORE INTO terms VALUES(?,?,?,?,?)", term_rows
        )
        imported += len(tag_rows)
        db.commit()
    db.execute(
        "UPDATE tags SET kind=CASE (SELECT category FROM danbooru_tags d "
        "WHERE d.canonical=tags.canonical) "
        "WHEN 0 THEN 'general' WHEN 1 THEN 'artist' WHEN 3 THEN 'copyright' "
        "WHEN 4 THEN 'character' WHEN 5 THEN 'meta' ELSE kind END,kind_conflict=0 "
        "WHERE kind_conflict=1 AND canonical IN (SELECT canonical FROM danbooru_tags)"
    )
    db.commit()
    return {"imported": imported, "rejected": rejected, "deprecated_excluded": deprecated}


def _import_aliases(db: sqlite3.Connection, path: Path) -> dict:
    imported = rejected = inactive = 0
    for row in _rows(path, ["id", "antecedent_name", "consequent_name", "status"]):
        if row.get("status") != "active":
            inactive += 1
            continue
        antecedent = catalog._builder._text(row.get("antecedent_name"))
        consequent = catalog._builder._text(row.get("consequent_name"))
        danbooru_id = row.get("id")
        if not antecedent or not consequent or not isinstance(danbooru_id, int):
            rejected += 1
            continue
        if not db.execute(
            "SELECT 1 FROM danbooru_tags WHERE canonical=?", (consequent,)
        ).fetchone():
            rejected += 1
            continue
        db.execute(
            "INSERT OR REPLACE INTO danbooru_aliases VALUES(?,?,?,?)",
            (antecedent, consequent, danbooru_id, SOURCE_ID),
        )
        db.execute(
            "INSERT OR IGNORE INTO terms VALUES(?,?,?,?,?)",
            (catalog._builder.normalize(antecedent), consequent, antecedent, SOURCE_ID, "alias"),
        )
        imported += 1
    db.commit()
    return {"imported": imported, "rejected": rejected, "inactive_excluded": inactive}


def _import_implications(db: sqlite3.Connection, path: Path) -> tuple[dict, list[tuple[str, str]]]:
    imported = rejected = inactive = 0
    edges = []
    for row in _rows(path, ["id", "antecedent_name", "consequent_name", "status"]):
        if row.get("status") != "active":
            inactive += 1
            continue
        antecedent = catalog._builder._text(row.get("antecedent_name"))
        consequent = catalog._builder._text(row.get("consequent_name"))
        danbooru_id = row.get("id")
        if not antecedent or not consequent or not isinstance(danbooru_id, int):
            rejected += 1
            continue
        if not db.execute(
            "SELECT count(*) FROM danbooru_tags WHERE canonical IN (?,?)",
            (antecedent, consequent),
        ).fetchone()[0] == 2:
            rejected += 1
            continue
        db.execute(
            "INSERT OR REPLACE INTO danbooru_implications VALUES(?,?,?,?)",
            (antecedent, consequent, danbooru_id, SOURCE_ID),
        )
        edges.append((antecedent, consequent))
        imported += 1
    db.commit()
    return {"imported": imported, "rejected": rejected, "inactive_excluded": inactive}, edges


def _build_groups(db: sqlite3.Connection, edges: list[tuple[str, str]]) -> dict:
    groups = [
        (
            "studio:footwear_relations", "鞋类（Danbooru 关系）", "group:attire",
            SOURCE_ID, "danbooru_active_implication",
        ),
        (
            "studio:footwear_candidates", "鞋类候选（至少 10 图，未审核）", "group:attire",
            SOURCE_ID, "heuristic_unreviewed",
        ),
    ]
    db.executemany(
        "INSERT INTO groups(id,label_zh,parent_id,source,review_status) VALUES(?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET label_zh=excluded.label_zh,parent_id=excluded.parent_id,"
        "source=excluded.source,review_status=excluded.review_status",
        groups,
    )
    for group_id, *_ in groups:
        db.execute("DELETE FROM tag_groups WHERE group_id=?", (group_id,))

    relation_tags = _relation_descendants(edges, RELATION_FOOTWEAR_ROOTS)
    relation_tags = {
        tag for tag in relation_tags
        if db.execute(
            "SELECT 1 FROM danbooru_tags WHERE canonical=? AND category=0", (tag,)
        ).fetchone()
    }
    db.executemany(
        "INSERT OR IGNORE INTO tag_groups VALUES('studio:footwear_relations',?)",
        ((tag,) for tag in sorted(relation_tags)),
    )

    candidates = [
        row[0]
        for row in db.execute(
            "SELECT canonical FROM danbooru_tags WHERE category=0 AND post_count>=? "
            "ORDER BY canonical",
            (CANDIDATE_MIN_POST_COUNT,),
        )
        if catalog.FOOTWEAR_PATTERN.search(row[0])
    ]
    db.executemany(
        "INSERT OR IGNORE INTO tag_groups VALUES('studio:footwear_candidates',?)",
        ((tag,) for tag in candidates),
    )
    return {
        "footwear_relation_tags": len(relation_tags),
        "footwear_candidates": len(candidates),
        "footwear_candidate_min_post_count": CANDIDATE_MIN_POST_COUNT,
    }


def augment(source_dir: Path, output_dir: Path) -> dict:
    source_dir, output_dir = source_dir.resolve(), output_dir.resolve()
    metadata, files = _verify(source_dir)
    manifest_path, manifest, active_name, active_database = catalog._active(output_dir)
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
            db.execute("PRAGMA cache_size=-65536")
            db.execute("PRAGMA synchronous=NORMAL")
            db.create_function("normalize", 1, catalog._builder.normalize, deterministic=True)
            db.executescript(catalog.EXTRA_SCHEMA)
            db.executescript(FULL_SCHEMA)
            db.execute("DELETE FROM terms WHERE source=?", (SOURCE_ID,))
            db.execute("DELETE FROM danbooru_aliases")
            db.execute("DELETE FROM danbooru_implications")
            db.execute("DELETE FROM danbooru_tags")
            tags = _import_tags(db, source_dir / "tags.parquet")
            tags["unique_tags"] = db.execute(
                "SELECT count(*) FROM danbooru_tags"
            ).fetchone()[0]
            aliases = _import_aliases(db, source_dir / "tag_aliases.parquet")
            implications, edges = _import_implications(
                db, source_dir / "tag_implications.parquet"
            )
            semantic = _build_groups(db, edges)
            catalog._builder._finish_works(db)
            catalog._builder._finish_groups(db)
            db.executescript("""
                CREATE INDEX IF NOT EXISTS tags_kind_canonical ON tags(kind,canonical);
                CREATE INDEX IF NOT EXISTS tags_count_canonical ON tags(count DESC,canonical);
                CREATE INDEX IF NOT EXISTS terms_canonical_role ON terms(canonical,role,text);
                CREATE INDEX IF NOT EXISTS tag_groups_canonical ON tag_groups(canonical,group_id);
                CREATE INDEX IF NOT EXISTS works_term_name ON works(term,name);
                CREATE INDEX IF NOT EXISTS tag_works_canonical_work ON tag_works(canonical,work);
                CREATE INDEX IF NOT EXISTS danbooru_tags_category_count
                    ON danbooru_tags(category,post_count DESC,canonical);
                CREATE INDEX IF NOT EXISTS danbooru_aliases_consequent
                    ON danbooru_aliases(consequent,antecedent);
                CREATE INDEX IF NOT EXISTS danbooru_implications_consequent
                    ON danbooru_implications(consequent,antecedent);
            """)
            db.execute("UPDATE metadata SET value=? WHERE key='version'", (version,))
            counts = catalog._builder._counts(db)
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
        source = {
            "id": SOURCE_ID,
            "path": f"baton4ik/danbooru-tag-metadata-snapshot@{REVISION}",
            "location": "external_verified_cache",
            "snapshot_verified": True,
            "revision": REVISION,
            "license": "MIT",
            "upstream": UPSTREAM,
            "snapshot_built_at": metadata["snapshot_built_at"],
            "files": files,
            "tags": tags,
            "aliases": aliases,
            "implications": implications,
            **semantic,
        }
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
        sources = [item for item in manifest.get("sources", []) if item.get("id") != SOURCE_ID]
        sources.append(source)
        updated["sources"] = sources
        coverage = dict(manifest.get("coverage", {}))
        coverage.update(
            scope="union_with_full_active_danbooru_tag_metadata_snapshot",
            official_category=(
                "Danbooru API category values for 1707139 unique, valid, non-deprecated "
                "tags from the 2026-04-08 snapshot"
            ),
            catalog_snapshot=(
                "1711015 source rows; 3862 deprecated and 6 malformed names excluded; "
                "8 duplicate canonical rows deduplicated"
            ),
            aliases=(
                "38207 active Danbooru aliases imported; 1 unresolved target excluded; "
                "local curated aliases retained"
            ),
            implications="41149 active Danbooru implications retained for semantic navigation",
            semantic_navigation=(
                "curated local groups plus clearly labeled relation-derived and heuristic groups"
            ),
            footwear_candidates=(
                "name-boundary heuristic with post_count>=10; explicitly unreviewed and "
                "separate from curated and implication-derived shoes"
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
    parser.add_argument("source_dir", type=Path)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "resources/danbooru_library"
    )
    args = parser.parse_args()
    try:
        result = augment(args.source_dir, args.output_dir)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        parser.exit(1, f"Full catalog augmentation failed; active manifest preserved: {exc}\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
