"""Explicit offline import; only the selected output directory is written.

python -B scripts/build_tag_library.py --source-package PACKAGE --output-dir OUTPUT
--small omits the normalized character table, artists, and cooccurrence edges.
Readers keep old immutable versions; this script never removes published versions.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("_public_tag_library", ROOT / "prompt_studio/tag_library.py")
_library = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_library)
normalize = _library.normalize
SCHEMA_VERSION = _library.SCHEMA_VERSION
_BUILD_LOCK = threading.Lock()
LOG = logging.getLogger("tag_library.build")
MAX_LINE = 2 * 1024 * 1024
SMALL_JSON_LIMIT = 8 * 1024 * 1024

SOURCES = (
    ("autocomplete", "prompt_studio/storage/autocomplete/autocomplete.txt", False),
    ("bilingual", "prompt_studio/storage/local_complete_tags/danbooru-0-zh.csv", False),
    ("clothing", "resources/Danbooru\u670d\u88c5\u67e5\u8be2\u8d44\u6e90_\u672c\u5730\u7248_2026-05-26.jsonl", False),
    ("characters", "resources/danbooru_character_webui.normalized.jsonl", True),
    ("artists", "resources/danbooru_artist_wildcard-D\u7ad9\u753b\u5e08\u5217\u8868.txt", True),
    ("manual_aliases", "resources/danbooru_character_aliases.json", False),
    ("cooccurrence", "resources/danbooru_tags_cooccurrence.csv", True),
)

QUICK_GROUP_SEMANTIC_MAP = {
    "人物": "studio:appearance",
    "服饰": "group:attire",
    "表情动作": "studio:expression",
    "画面": "studio:style",
    "环境": "studio:environment",
    "场景": "studio:environment",
    "物品": "studio:objects",
    "镜头": "studio:composition",
}

POSE_QUICK_GROUPS = {
    "基础动作", "手部动作", "手部动作(拿着某物)", "手部动作(放在某地)",
    "手部动作(抓着某物)", "腿部动作", "其他动作",
}

COUNT_QUICK_GROUPS = {"对象", "人数", "数量"}
DANBOORU_CATEGORIES = {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta"}

SCHEMA = """
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE tags(
 canonical TEXT PRIMARY KEY,label_zh TEXT NOT NULL DEFAULT '',kind TEXT NOT NULL DEFAULT 'unknown',
 count INTEGER,count_source TEXT,count_scope TEXT,description TEXT NOT NULL DEFAULT '',
 label_source TEXT NOT NULL DEFAULT '',label_priority INTEGER NOT NULL DEFAULT 0,
 count_priority INTEGER NOT NULL DEFAULT 0,kind_conflict INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
CREATE TABLE terms(
 term TEXT NOT NULL,canonical TEXT NOT NULL REFERENCES tags(canonical),text TEXT NOT NULL,
 source TEXT NOT NULL,role TEXT NOT NULL,PRIMARY KEY(term,canonical,source,role)
) WITHOUT ROWID;
CREATE TABLE tag_sources(
 canonical TEXT NOT NULL REFERENCES tags(canonical),source TEXT NOT NULL,payload TEXT NOT NULL,
 PRIMARY KEY(canonical,source)
) WITHOUT ROWID;
CREATE TABLE groups(
 id TEXT PRIMARY KEY,label_zh TEXT NOT NULL,parent_id TEXT,source TEXT NOT NULL,
 review_status TEXT NOT NULL,count INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
CREATE TABLE tag_groups(
 group_id TEXT NOT NULL REFERENCES groups(id),canonical TEXT NOT NULL REFERENCES tags(canonical),
 PRIMARY KEY(group_id,canonical)
) WITHOUT ROWID;
CREATE TABLE works(
 name TEXT PRIMARY KEY,term TEXT NOT NULL,label_zh TEXT NOT NULL DEFAULT '',
 post_count INTEGER,character_count INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
CREATE TABLE tag_works(
 work TEXT NOT NULL REFERENCES works(name),canonical TEXT NOT NULL REFERENCES tags(canonical),
 PRIMARY KEY(work,canonical)
) WITHOUT ROWID;
CREATE TABLE edges(
 a TEXT NOT NULL,b TEXT NOT NULL,count INTEGER NOT NULL CHECK(count>0),
 PRIMARY KEY(a,b),CHECK(a<b)
) WITHOUT ROWID;
"""

TAG_UPSERT = """
INSERT INTO tags(canonical,label_zh,kind,count,count_source,count_scope,description,
                 label_source,label_priority,count_priority)
VALUES(?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(canonical) DO UPDATE SET
 label_zh=CASE WHEN excluded.label_priority>tags.label_priority THEN excluded.label_zh ELSE tags.label_zh END,
 label_source=CASE WHEN excluded.label_priority>tags.label_priority THEN excluded.label_source ELSE tags.label_source END,
 label_priority=max(tags.label_priority,excluded.label_priority),
 count=CASE WHEN excluded.count_priority>tags.count_priority THEN excluded.count ELSE tags.count END,
 count_source=CASE WHEN excluded.count_priority>tags.count_priority THEN excluded.count_source ELSE tags.count_source END,
 count_scope=CASE WHEN excluded.count_priority>tags.count_priority THEN excluded.count_scope ELSE tags.count_scope END,
 count_priority=max(tags.count_priority,excluded.count_priority),
 description=CASE WHEN tags.description='' THEN excluded.description ELSE tags.description END,
 kind=CASE WHEN tags.kind_conflict=1 THEN 'unknown'
           WHEN tags.kind='unknown' THEN excluded.kind
           WHEN excluded.kind='unknown' OR tags.kind=excluded.kind THEN tags.kind ELSE 'unknown' END,
 kind_conflict=CASE WHEN tags.kind!='unknown' AND excluded.kind!='unknown' AND tags.kind!=excluded.kind
                    THEN 1 ELSE tags.kind_conflict END
"""


def _text(value, maximum=512):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        return ""
    # Reject lone surrogates rather than failing later inside sqlite3.
    try:
        value.encode("utf-8")
    except UnicodeError:
        return ""
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _label(value):
    value = _text(value)
    return value if re.search(r"[\u3400-\u9fff]", value) else ""


def _count(value):
    if isinstance(value, bool):
        return None
    value = str(value).strip()
    if not re.fullmatch(r"[0-9]+(?:\.0+)?", value) or len(value) > 40:
        return None
    count = int(value.partition(".")[0])
    return count if count <= 9223372036854775807 else None


def _source(path: Path, root: Path, source_id: str, location="source_package"):
    result = {"id": source_id, "path": path.relative_to(root).as_posix(), "location": location,
              "size": None, "mtime": None, "mtime_ns": None, "sha256": None,
              "imported": 0, "rejected": 0, "status": "missing",
              "snapshot_verified": False}
    if path.is_file():
        stat = path.stat()
        result.update(size=stat.st_size, mtime=stat.st_mtime, mtime_ns=stat.st_mtime_ns, status="pending")
    return result


@contextmanager
def _lines(path, source):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        def iterate():
            first = True
            while True:
                raw = stream.readline(MAX_LINE + 1)
                if not raw:
                    break
                digest.update(raw)
                if len(raw) > MAX_LINE:
                    while raw and not raw.endswith(b"\n"):
                        raw = stream.readline(MAX_LINE + 1)
                        digest.update(raw)
                    source["rejected"] += 1
                    continue
                try:
                    line = raw.decode("utf-8-sig" if first else "utf-8")
                except UnicodeError:
                    source["rejected"] += 1
                    first = False
                    continue
                first = False
                yield line
        yield iterate()
    stat = path.stat()
    if stat.st_size != source["size"] or stat.st_mtime_ns != source["mtime_ns"]:
        raise ValueError(f"source changed during build: {source['path']}")
    source.update(sha256=digest.hexdigest(), status="imported")


def _json_rows(lines, source):
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            source["rejected"] += 1
            continue
        if not isinstance(row, dict):
            source["rejected"] += 1
            continue
        yield row


def _csv_rows(lines, source):
    reader = csv.reader(lines, strict=True)
    while True:
        try:
            row = next(reader)
        except StopIteration:
            return
        except csv.Error:
            source["rejected"] += 1
            continue
        if row:
            yield row


class _Importer:
    def __init__(self, db):
        self.db = db
        self.records = 0

    def accepted(self, source):
        source["imported"] += 1
        self.records += 1
        if self.records % 5000 == 0:
            self.db.commit()

    def term(self, canonical, value, source, role="alias"):
        value = _text(value)
        if value:
            self.db.execute("INSERT OR IGNORE INTO terms VALUES(?,?,?,?,?)",
                            (normalize(value), canonical, value, source, role))

    def tag(self, canonical, source, label="", kind="unknown", count=None, priority=0,
            description="", aliases=(), payload=None, count_priority=None,
            count_scope="source_snapshot_unverified", official_category=None):
        canonical = _text(canonical)
        if not canonical:
            return False
        label = _label(label)
        if count_priority is None:
            count_priority = (100 if source == "autocomplete" else 50) if count is not None else 0
        self.db.execute(TAG_UPSERT, (canonical, label, kind, count, source if count is not None else None,
                                    count_scope if count is not None else None,
                                    _text(description, 16000), source if label else "",
                                    priority if label else 0, count_priority))
        evidence = dict(payload or {})
        evidence.update(kind_evidence=kind, official_category=official_category)
        if count is not None:
            evidence.update(count=count, count_scope=count_scope)
        self.db.execute("INSERT OR IGNORE INTO tag_sources VALUES(?,?,?)",
                        (canonical, source, json.dumps(evidence, ensure_ascii=True)))
        self.term(canonical, canonical, source, "canonical")
        if label:
            self.term(canonical, label, source, "label")
        for alias in aliases:
            self.term(canonical, alias, source)
        return True

    def work(self, work, canonical, label="", post_count=None):
        work, canonical = _text(work), _text(canonical)
        if not work or not canonical:
            return False
        self.db.execute(
            "INSERT INTO works(name,term,label_zh,post_count) VALUES(?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "label_zh=CASE WHEN works.label_zh='' THEN excluded.label_zh ELSE works.label_zh END," 
            "post_count=CASE WHEN excluded.post_count IS NOT NULL THEN excluded.post_count ELSE works.post_count END",
            (work, normalize(work), _label(label), post_count),
        )
        self.db.execute("INSERT OR IGNORE INTO tag_works VALUES(?,?)", (work, canonical))
        return True

    def group(self, group_id, label, parent, source, review="source_mapping_unverified"):
        group_id = _text(group_id)
        if not group_id:
            return False
        self.db.execute("INSERT INTO groups(id,label_zh,parent_id,source,review_status) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(id) DO NOTHING",
                        (group_id, _text(label) or group_id, _text(parent) or None, source, _text(review)))
        return True

    def autocomplete(self, lines, source):
        for row in _csv_rows(lines, source):
            count = _count(row[1]) if len(row) == 2 else None
            if count is None or not self.tag(row[0], source["id"], count=count):
                source["rejected"] += 1
            else:
                self.accepted(source)

    def bilingual(self, lines, source):
        for row in _csv_rows(lines, source):
            if len(row) != 2 or not _text(row[0]) or not _text(row[1]):
                source["rejected"] += 1
                continue
            if row[0].strip().lower() in {"tag", "canonical", "name"} and row[1].strip().lower() in {"zh", "label_zh", "translation"}:
                continue
            aliases = [_label(part) for part in re.split(r"[|/;\uff1b]", row[1])]
            self.tag(row[0], source["id"], label=row[1], priority=50, aliases=aliases,
                     payload={"translation": row[1], "review_status": "source_unverified"})
            self.accepted(source)

    def clothing(self, lines, source):
        for row in _json_rows(lines, source):
            parents = row.get("parent_ids", [])
            parents = [p for p in parents if _text(p)] if isinstance(parents, list) else []
            if row.get("node_type") in {"root", "group"}:
                ok = self.group(row.get("id"), row.get("name_zh"), parents[0] if parents else None, source["id"])
            elif row.get("node_type") == "tag":
                canonical = _text(row.get("tag"))
                ok = self.tag(canonical, source["id"], label=row.get("name_zh"), priority=90,
                              description=row.get("definition", ""))
                if ok:
                    for parent in parents:
                        self.db.execute("INSERT OR IGNORE INTO tag_groups VALUES(?,?)", (parent, canonical))
            else:
                ok = False
            if ok:
                self.accepted(source)
            else:
                source["rejected"] += 1

    def characters(self, lines, source):
        for row in _json_rows(lines, source):
            canonical = row.get("canonical", row.get("character\u89d2\u8272"))
            display = _text(row.get("display_zh", row.get("character\u89d2\u8272_2", "")))
            label = re.split(r"\s+[\u2013\u2014-]\s+", display, maxsplit=1)[0]
            aliases = row.get("aliases", [])
            aliases = list(aliases) if isinstance(aliases, list) else []
            aliases.extend([display, label])
            count = _count(row.get("count"))
            copyright_text = _text(row.get("copyright", row.get("copyright\u4f5c\u54c1")))
            payload = {"display_name": display, "copyright": copyright_text,
                       "url": _text(row.get("url"), 4096), "solo_count": _count(row.get("solo_count")),
                       "review_status": "source_unverified"}
            if self.tag(canonical, source["id"], label=label, kind="character", count=count,
                        priority=80, aliases=aliases, payload=payload):
                if isinstance(row.get("aliases"), list):
                    source["records_with_aliases_field"] = source.get("records_with_aliases_field", 0) + 1
                if copyright_text:
                    self.work(copyright_text, _text(canonical))
                self.accepted(source)
            else:
                source["rejected"] += 1

    def artists(self, lines, source):
        for row in _csv_rows(lines, source):
            if row[0].strip() == "artist" and len(row) > 1 and row[1].strip() == "trigger":
                continue
            if self.tag(row[0], source["id"], kind="artist", aliases=row[1:2],
                        payload={"trigger": _text(row[1]) if len(row) > 1 else ""}):
                self.accepted(source)
            else:
                source["rejected"] += 1

    def danbooru_snapshot(self, lines, source):
        for row in _json_rows(lines, source):
            canonical = _text(row.get("name"))
            category = row.get("category")
            count = _count(row.get("post_count"))
            if (not canonical or isinstance(category, bool) or category not in DANBOORU_CATEGORIES
                    or count is None or row.get("is_deprecated") is not False):
                source["rejected"] += 1
                continue
            kind = DANBOORU_CATEGORIES[category]
            payload = {
                "id": row.get("id"),
                "created_at": _text(row.get("created_at")),
                "updated_at": _text(row.get("updated_at")),
                "is_deprecated": False,
                "dataset_revision": source.get("revision", ""),
                "review_status": "third_party_snapshot_of_danbooru_api",
            }
            if not self.tag(
                canonical,
                source["id"],
                kind=kind,
                count=count,
                count_priority=120,
                count_scope="qdlabs_filtered_danbooru_snapshot",
                official_category=category,
                payload=payload,
            ):
                source["rejected"] += 1
                continue
            # The snapshot carries Danbooru's category field and resolves older
            # source-inference conflicts without replacing curated labels.
            self.db.execute(
                "UPDATE tags SET kind=?,kind_conflict=0 WHERE canonical=?",
                (kind, canonical),
            )
            if kind == "copyright":
                self.work(canonical, canonical, post_count=count)
            self.accepted(source)

    def manual_aliases(self, lines, source):
        # Only the explicitly bounded small, curated JSON is materialized.
        payload = json.loads("".join(lines))
        if not isinstance(payload, dict):
            raise ValueError("manual alias JSON must be an object")
        for canonical, row in payload.items():
            if not isinstance(row, dict):
                source["rejected"] += 1
                continue
            aliases = row.get("aliases", [])
            aliases = aliases if isinstance(aliases, list) else []
            label = _label(row.get("display_zh")) or next((_label(a) for a in aliases if _label(a)), "")
            blocked = row.get("blocked_tags", [])
            blocked = [v for v in blocked if _text(v)] if isinstance(blocked, list) else []
            if self.tag(canonical, source["id"], label=label, kind="character", priority=100,
                        aliases=[a for a in aliases if a not in blocked], payload={"blocked_tags": blocked}):
                self.accepted(source)
            else:
                source["rejected"] += 1

    def cooccurrence(self, lines, source):
        batch = []
        for row in _csv_rows(lines, source):
            if row == ["tag_a", "tag_b", "count"]:
                continue
            if len(row) != 3:
                source["rejected"] += 1
                continue
            a, b, count = _text(row[0]), _text(row[1]), _count(row[2])
            if not a or not b or a == b or count is None or count <= 0:
                source["rejected"] += 1
                continue
            if a > b:
                a, b = b, a
            batch.append((a, b, count))
            source["imported"] += 1
            if len(batch) >= 5000:
                self._edges(batch)
                batch.clear()
        self._edges(batch)
        source["unique_edges"] = self.db.execute("SELECT count(*) FROM edges").fetchone()[0]
        source["duplicate_or_reverse_records"] = source["imported"] - source["unique_edges"]
        self.db.execute("INSERT OR IGNORE INTO tags(canonical) SELECT a FROM edges UNION SELECT b FROM edges")
        self.db.execute("INSERT OR IGNORE INTO tag_sources SELECT canonical,?,? FROM "
                        "(SELECT a AS canonical FROM edges UNION SELECT b FROM edges)",
                        (source["id"], json.dumps({"role": "cooccurrence_endpoint", "official_category": None})))
        self.db.execute("INSERT OR IGNORE INTO terms SELECT normalize(canonical),canonical,canonical,?,'canonical' "
                        "FROM tag_sources WHERE source=?", (source["id"], source["id"]))

    def _edges(self, batch):
        self.db.executemany("INSERT INTO edges VALUES(?,?,?) ON CONFLICT(a,b) "
                            "DO UPDATE SET count=max(edges.count,excluded.count)", batch)
        self.db.commit()


def _import_categories(importer, path, root, location):
    source = _source(path, root, "categories", location)
    if source["status"] == "missing":
        return source
    if source["size"] > SMALL_JSON_LIMIT:
        raise ValueError("category skeleton is too large")
    with _lines(path, source) as lines:
        payload = json.loads("".join(lines))
    if not isinstance(payload, dict) or not isinstance(payload.get("semantic_groups"), list):
        raise ValueError("invalid category skeleton")
    for row in payload["semantic_groups"]:
        if isinstance(row, dict) and importer.group(row.get("id"), row.get("label_zh"), row.get("parent_id"),
                                                     _text(row.get("source")) or "categories",
                                                     _text(row.get("review_status")) or "source_unverified"):
            source["imported"] += 1
        else:
            source["rejected"] += 1
    return source


def _semantic_group_for_quick_group(section_name, group_name):
    if section_name == "表情动作" and group_name in POSE_QUICK_GROUPS:
        return "studio:pose"
    if section_name == "人物" and group_name in COUNT_QUICK_GROUPS:
        return "studio:count"
    if section_name == "画面":
        if group_name == "光照":
            return "studio:lighting"
        if group_name == "颜色":
            return "studio:color"
        if group_name == "背景":
            return "studio:environment"
    return QUICK_GROUP_SEMANTIC_MAP.get(section_name)


def _canonical_for_quick_tag(db, raw_tag):
    term = normalize(_text(raw_tag).replace("\\(", "(").replace("\\)", ")"))
    if not term:
        return None
    rows = db.execute(
        "SELECT DISTINCT canonical FROM terms WHERE term=? ORDER BY canonical LIMIT 3",
        (term,),
    ).fetchall()
    if not rows:
        return None
    exact = [row[0] for row in rows if normalize(row[0]) == term]
    if len(exact) == 1:
        return exact[0]
    return rows[0][0] if len(rows) == 1 else None


def _import_quick_group_semantics(importer, path, root):
    source = _source(path, root, "quick_group_semantics")
    if source["status"] == "missing":
        return source
    if source["size"] > SMALL_JSON_LIMIT:
        source.update(status="skipped", reason="exceeds_safe_yaml_limit")
        return source
    try:
        import yaml
    except ImportError:
        source.update(status="skipped", reason="pyyaml_not_installed")
        return source

    with _lines(path, source) as lines:
        payload = yaml.safe_load("".join(lines))
    if not isinstance(payload, list):
        raise ValueError("quick group semantic source must be a list")

    seen = set()
    unmatched = 0
    ignored_sections = 0
    for section in payload:
        if not isinstance(section, dict):
            continue
        section_name = _text(section.get("name"))
        if section_name not in QUICK_GROUP_SEMANTIC_MAP:
            ignored_sections += 1
            continue
        for group in section.get("groups", []):
            if not isinstance(group, dict) or not isinstance(group.get("tags"), dict):
                continue
            group_name = _text(group.get("name"))
            semantic_group = _semantic_group_for_quick_group(section_name, group_name)
            if not semantic_group:
                continue
            for raw_tag in group["tags"]:
                canonical = _canonical_for_quick_tag(importer.db, raw_tag)
                membership = (semantic_group, canonical)
                if not canonical:
                    unmatched += 1
                    continue
                if membership in seen:
                    continue
                seen.add(membership)
                importer.db.execute("INSERT OR IGNORE INTO tag_groups VALUES(?,?)", membership)
                importer.db.execute(
                    "INSERT OR IGNORE INTO tag_sources VALUES(?,?,?)",
                    (
                        canonical,
                        source["id"],
                        json.dumps(
                            {
                                "role": "semantic_navigation_seed",
                                "section": section_name,
                                "group": group_name,
                                "review_status": "local_quick_group_seed",
                            },
                            ensure_ascii=True,
                        ),
                    ),
                )
                source["imported"] += 1
    source.update(unmatched=unmatched, ignored_sections=ignored_sections)
    return source


def _finish_groups(db):
    db.execute("INSERT OR IGNORE INTO groups(id,label_zh,source,review_status) "
               "SELECT DISTINCT group_id,group_id,'clothing','missing_group_definition' FROM tag_groups")
    parents = dict(db.execute("SELECT id,parent_id FROM groups"))
    for group_id in parents:
        visited = {group_id}
        parent = parents[group_id]
        while parent:
            if parent in visited:
                raise ValueError("cycle in semantic groups")
            if parent not in parents:
                raise ValueError("unknown semantic group parent")
            visited.add(parent)
            db.execute("INSERT OR IGNORE INTO tag_groups SELECT ?,canonical FROM tag_groups WHERE group_id=?",
                       (parent, group_id))
            parent = parents[parent]
    db.execute("UPDATE groups SET count=(SELECT count(*) FROM tag_groups WHERE group_id=groups.id)")


def _finish_works(db):
    db.execute(
        "INSERT OR IGNORE INTO works(name,term) "
        "SELECT DISTINCT json_extract(payload,'$.copyright'),"
        "normalize(json_extract(payload,'$.copyright')) FROM tag_sources "
        "WHERE source='characters' AND coalesce(json_extract(payload,'$.copyright'),'')!=''"
    )
    db.execute(
        "INSERT OR IGNORE INTO tag_works(work,canonical) "
        "SELECT json_extract(payload,'$.copyright'),canonical FROM tag_sources "
        "WHERE source='characters' AND coalesce(json_extract(payload,'$.copyright'),'')!=''"
    )
    db.execute(
        "INSERT INTO works(name,term,label_zh,post_count) "
        "SELECT canonical,normalize(canonical),label_zh,count FROM tags WHERE kind='copyright' "
        "ON CONFLICT(name) DO UPDATE SET "
        "label_zh=CASE WHEN works.label_zh='' THEN excluded.label_zh ELSE works.label_zh END," 
        "post_count=coalesce(excluded.post_count,works.post_count)"
    )
    db.execute(
        "INSERT OR IGNORE INTO tag_works(work,canonical) "
        "SELECT canonical,canonical FROM tags WHERE kind='copyright'"
    )
    db.execute(
        "UPDATE works SET character_count=(SELECT count(*) FROM tag_works "
        "WHERE tag_works.work=works.name AND tag_works.canonical!=works.name)"
    )


def _counts(db):
    result = {key: db.execute(sql).fetchone()[0] for key, sql in {
        "tag_count": "SELECT count(*) FROM tags",
        "translated_count": "SELECT count(*) FROM tags WHERE label_zh!=''",
        "alias_count": "SELECT count(*) FROM terms WHERE role='alias'",
        "ambiguous_term_count": "SELECT count(*) FROM (SELECT term FROM terms GROUP BY term HAVING count(DISTINCT canonical)>1)",
        "group_count": "SELECT count(*) FROM groups",
        "group_membership_count": "SELECT count(*) FROM tag_groups",
        "cooccurrence_count": "SELECT count(*) FROM edges",
        "kind_conflict_count": "SELECT count(*) FROM tags WHERE kind_conflict=1",
    }.items()}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='works'").fetchone():
        result["work_count"] = db.execute("SELECT count(*) FROM works").fetchone()[0]
        result["work_membership_count"] = db.execute("SELECT count(*) FROM tag_works").fetchone()[0]
    result["by_kind"] = dict(db.execute("SELECT kind,count(*) FROM tags GROUP BY kind"))
    return result


def build_library(source_package: Path, output_dir: Path, include_large=True,
                  allow_coverage_regression=False) -> dict:
    """Return the published manifest. Missing resources degrade; zero tags fails.

    Sources are UTF-8. Individual malformed records are counted and skipped;
    invalid small JSON, source mutation, SQLite/I/O errors abort publication.
    SQLite sort scratch is explicitly redirected to OUTPUT, never system TEMP.
    """
    source_package, output_dir = Path(source_package).resolve(), Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with _BUILD_LOCK:
        lock = output_dir / "build.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ValueError("build.lock exists; concurrent builds are forbidden. "
                             "For a stale lock, verify its process has stopped before removing it manually") from exc
        identity = os.fstat(descriptor)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "created_at": datetime.now(timezone.utc).isoformat()}, stream)
            return _build(
                source_package,
                output_dir,
                include_large,
                allow_coverage_regression=allow_coverage_regression,
            )
        finally:
            if lock.exists():
                current = lock.stat()
                if (identity.st_dev, identity.st_ino) == (current.st_dev, current.st_ino):
                    lock.unlink()


def _build(source_package, output_dir, include_large, allow_coverage_regression=False):
    started = time.monotonic()
    version = uuid.uuid4().hex
    temporary = output_dir / f".catalog-{version}.building.sqlite3"
    database = output_dir / f"catalog-{version}.sqlite3"
    pointer_tmp = output_dir / f".manifest-{version}.tmp"
    db = None
    published = False
    previous_temp = ""
    try:
        db = sqlite3.connect(temporary)
        previous = db.execute("PRAGMA temp_store_directory").fetchone()
        previous_temp = previous[0] if previous else ""
        # SQLite's Windows VFS otherwise falls back to the C: user TEMP folder.
        quoted_output = str(output_dir).replace("'", "''")
        db.execute(f"PRAGMA temp_store_directory='{quoted_output}'")
        setting = db.execute("PRAGMA temp_store_directory").fetchone()
        if not setting or Path(setting[0]).resolve() != output_dir:
            raise ValueError("SQLite cannot place scratch files inside output_dir")
        db.execute("PRAGMA temp_store=FILE")
        db.execute("PRAGMA cache_size=-32768")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.create_function("normalize", 1, normalize, deterministic=True)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO metadata VALUES('version',?)", (version,))
        importer = _Importer(db)
        category_path = output_dir / "categories.v1.json"
        category_root, category_location = output_dir, "output_dir"
        if not category_path.is_file():
            category_path = source_package / "resources/danbooru_library/categories.v1.json"
            category_root, category_location = source_package, "source_package"
        sources = [_import_categories(importer, category_path, category_root, category_location)]
        for source_id, relative, large in SOURCES:
            path = source_package / relative
            source = _source(path, source_package, source_id)
            sources.append(source)
            if source["status"] == "missing":
                continue
            if large and not include_large:
                source.update(status="skipped", reason="small_build")
                continue
            if source_id == "manual_aliases" and source["size"] > SMALL_JSON_LIMIT:
                source.update(status="skipped", reason="exceeds_small_json_limit")
                continue
            LOG.info("Importing %s (%s bytes)", source_id, source["size"])
            with _lines(path, source) as lines:
                getattr(importer, source_id)(lines, source)
            db.commit()
            LOG.info("Imported %s: accepted=%s rejected=%s", source_id, source["imported"], source["rejected"])
        quick_group_path = source_package / "prompt_studio/storage/group_tags/zh_CN.yaml"
        quick_group_source = _import_quick_group_semantics(importer, quick_group_path, source_package)
        sources.append(quick_group_source)
        db.commit()
        LOG.info("Building group memberships and query indexes")
        _finish_groups(db)
        _finish_works(db)
        db.executescript("""
            CREATE INDEX tags_kind_canonical ON tags(kind,canonical);
            CREATE INDEX tags_count_canonical ON tags(count DESC,canonical);
            CREATE INDEX terms_canonical_role ON terms(canonical,role,text);
            CREATE INDEX tag_groups_canonical ON tag_groups(canonical,group_id);
            CREATE INDEX works_term_name ON works(term,name);
            CREATE INDEX tag_works_canonical_work ON tag_works(canonical,work);
            CREATE INDEX edges_a_top ON edges(a,count DESC,b);
            CREATE INDEX edges_b_top ON edges(b,count DESC,a);
        """)
        counts = _counts(db)
        if not counts["tag_count"]:
            raise ValueError("no valid tags imported; existing library was not replaced")
        current_manifest_path = output_dir / "manifest.json"
        if current_manifest_path.is_file() and not allow_coverage_regression:
            try:
                current_manifest = json.loads(current_manifest_path.read_text(encoding="utf-8"))
                current_count = int(current_manifest.get("counts", {}).get("tag_count", 0) or 0)
            except (OSError, UnicodeError, ValueError, TypeError):
                current_count = 0
            if current_count > counts["tag_count"]:
                missing = [source["id"] for source in sources if source.get("status") == "missing"]
                raise ValueError(
                    "refusing tag-library coverage regression: "
                    f"current={current_count}, rebuilt={counts['tag_count']}, missing_sources={missing}. "
                    "Restore the source resources or pass --allow-coverage-regression explicitly."
                )
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("library validation failed")
        db.execute("ANALYZE")
        db.commit()
        db.execute("PRAGMA temp_store_directory='" + previous_temp.replace("'", "''") + "'")
        db.close()
        db = None
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        manifest = {"schema_version": SCHEMA_VERSION, "version": version, "database": database.name,
                    "built_at": datetime.now(timezone.utc).isoformat(), "build_seconds": round(time.monotonic() - started, 3),
                    "counts": counts, "sources": sources, "include_large": bool(include_large),
                    "database_size": temporary.stat().st_size,
                    "database_sha256": _file_sha256(temporary),
                    "coverage": {"scope": "local_resource_candidate_union_not_official_complete_export",
                                 "official_category": "not_available; kind is source-backed only",
                                 "aliases": "normalized JSONL aliases when present, display names, bilingual variants, artist triggers, small manual JSON",
                                 "generated_aliases": "not_imported: danbooru_character_aliases.generated.json",
                                 "tag_count_resources": "not_imported: corpus provenance unverified",
                                 "copyright": "source metadata only; not split into inferred canonical tags",
                                 "counts": "source_snapshot_unverified; never summed across sources",
                                 "cooccurrence": "undirected max per pair; both endpoints indexed"},
                    "search_order": "canonical_ascending", "max_limit": 100,
                    "update_policy": "atomic_manifest_pointer; old_versions_retained",
                    "scratch_policy": "output_directory_only; allow space for new database and sort scratch plus old versions"}
        os.replace(temporary, database)
        with pointer_tmp.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pointer_tmp, output_dir / "manifest.json")
        published = True
        return manifest
    finally:
        if db is not None:
            try:
                db.rollback()
                db.execute("PRAGMA temp_store_directory='" + previous_temp.replace("'", "''") + "'")
            finally:
                db.close()
        leftovers = [temporary, pointer_tmp, Path(str(temporary) + "-journal")]
        if not published:
            leftovers.append(database)
        for path in leftovers:
            if path.exists():
                path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-package", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "resources/danbooru_library")
    parser.add_argument("--small", action="store_true")
    parser.add_argument("--allow-coverage-regression", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        manifest = build_library(
            args.source_package,
            args.output_dir,
            include_large=not args.small,
            allow_coverage_regression=args.allow_coverage_regression,
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"Build failed; previous manifest preserved: {exc}\n")
    print(json.dumps(manifest, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
