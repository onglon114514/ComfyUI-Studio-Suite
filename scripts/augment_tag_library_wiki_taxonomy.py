"""Add a practical, human-oriented Danbooru Wiki attire taxonomy.

This maintainer-only tool reads a pinned Wiki snapshot with PyArrow, copies the
active immutable SQLite catalog, and atomically publishes a new version. The
runtime dictionary stays dependency-free and user quick-tag files are untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

import augment_tag_library_catalog as catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "danbooru_wiki_taxonomy_sakizuki"
REVISION = "10f97c0fdc749898c6393e08a97591338d1c6204"
UPSTREAM = "https://huggingface.co/datasets/SAkizuki/danbooru_wikis_2026"
EXPECTED_SHA256 = "eb4107e68654b7196d431ab5d7cf7ecd333f03a5d9637bd81219728507e0921a"
EXPECTED_ROWS = 268_908
WIKI_FILE = "wiki_pages_260310.parquet"

ROOT_GROUP = "wiki:attire"
ROOT_LABEL = "服装与配饰（Danbooru Wiki）"

# The page list is deliberately maintained by humans. It favors categories
# useful while writing prompts instead of attempting to mirror every Wiki page.
PAGE_SPECS = (
    ("tag_group:attire", ROOT_GROUP, "服装总分类", True),
    ("shirt", "wiki:attire:shirts_and_topwear", "上衣款式", False),
    ("sweater", "wiki:attire:shirts_and_topwear", "毛衣款式", False),
    ("jacket", "wiki:attire:shirts_and_topwear", "夹克款式", False),
    ("coat", "wiki:attire:shirts_and_topwear", "外套款式", False),
    ("tag_group:sleeves", "wiki:attire:shirts_and_topwear", "袖型细分", False),
    ("collar", "wiki:attire:shirts_and_topwear", "衣领细分", False),
    ("pants", "wiki:attire:pants_and_bottomwear", "裤装款式", False),
    ("shorts", "wiki:attire:pants_and_bottomwear", "短裤款式", False),
    ("skirt", "wiki:attire:pants_and_bottomwear", "裙装款式", False),
    ("tag_group:dress", "wiki:attire:one_piece", "连衣裙细分", False),
    ("dress", "wiki:attire:one_piece", "连衣裙外观与结构", False),
    ("tag_group:legwear", "wiki:attire:legs_and_feet", "腿袜总分类", False),
    ("socks", "wiki:attire:legs_and_feet", "袜子细分", False),
    ("thighhighs", "wiki:attire:legs_and_feet", "长筒袜细分", False),
    ("pantyhose", "wiki:attire:legs_and_feet", "连裤袜细分", False),
    ("shoes", "wiki:attire:shoes_and_footwear", "鞋类细分款式", False),
    ("boots", "wiki:attire:shoes_and_footwear", "靴子细分", False),
    ("tag_group:headwear", "wiki:attire:headwear_and_headgear", "头饰与帽类细分", False),
    ("tag_group:neck_and_neckwear", "wiki:attire:jewelry_and_accessories", "颈部与领饰", False),
    ("tag_group:handwear", "wiki:attire:jewelry_and_accessories", "手套与手部配饰", False),
    ("gloves", "wiki:attire:jewelry_and_accessories", "手套细分", False),
    ("tag_group:accessories", "wiki:attire:jewelry_and_accessories", "配饰总分类", False),
    ("earrings", "wiki:attire:jewelry_and_accessories", "耳饰细分", False),
    ("necklace", "wiki:attire:jewelry_and_accessories", "项链细分", False),
    ("swimsuit", "wiki:attire:swimsuits_and_bodysuits", "泳装细分", False),
    ("bikini", "wiki:attire:swimsuits_and_bodysuits", "比基尼细分", False),
    ("bodysuit", "wiki:attire:swimsuits_and_bodysuits", "紧身连体衣细分", False),
    ("tag_group:bra", "wiki:attire:underwear", "胸衣细分", False),
    ("tag_group:panties", "wiki:attire:underwear", "内裤细分", False),
    ("chinese_clothes", "wiki:attire:traditional_clothing", "中式服装", False),
    ("japanese_clothes", "wiki:attire:traditional_clothing", "日式服装", False),
    ("kimono", "wiki:attire:traditional_clothing", "和服细分", False),
    ("hanfu", "wiki:attire:traditional_clothing", "汉服细分", False),
    ("korean_clothes", "wiki:attire:traditional_clothing", "韩式服装", False),
    ("school_uniform", "wiki:attire:uniforms_and_costumes", "学校制服", False),
    ("military_uniform", "wiki:attire:uniforms_and_costumes", "军装细分", False),
    ("maid", "wiki:attire:uniforms_and_costumes", "女仆装细分", False),
    ("tag_group:fashion_style", "wiki:attire:styles_and_patterns", "时尚风格细分", False),
    # --- wiki:pose tree: posture hub + aggregated buckets (single-pose pages
    #     have no membership lists; the posture/gestures hubs carry the tags) ---
    ("tag_group:posture", "wiki:pose", "姿势总聚合", False),
    ("tag_group:gestures", "wiki:pose:hand_gestures", "手势聚合页", False),
    ("tag_group:hands", "wiki:pose:hand_gestures", "手部聚合页", False),
    ("tag_group:dances", "wiki:pose:dancing", "舞蹈聚合页", False),
    ("holding", "wiki:pose:holding", "持物总纲", False),
    ("carrying", "wiki:pose:holding", "搬运/背负细分", False),
    ("tag_group:sex_acts", "wiki:pose:sexual", "性行为聚合页（涩涩）", False),
    ("tag_group:sexual_positions", "wiki:pose:sexual", "性体位聚合页（涩涩）", False),
    ("tag_group:simulated_sex_acts", "wiki:pose:sexual", "模拟性行为聚合页", False),
    ("tag_group:sex_objects", "wiki:pose:sexual", "性道具聚合页", False),
    ("tag_group:sexual_attire", "wiki:pose:sexual", "性向服饰聚合页", False),
    ("sex", "wiki:pose:sexual", "性行为总纲（涩涩）", False),
    ("anal", "wiki:pose:sexual", "后入/肛交细分", False),
    ("oral", "wiki:pose:sexual", "口交细分", False),
    ("vaginal", "wiki:pose:sexual", "插入细分", False),
    ("masturbation", "wiki:pose:sexual", "自慰细分", False),
    ("handjob", "wiki:pose:sexual", "手交细分", False),
    ("footjob", "wiki:pose:sexual", "足交细分", False),
    ("fellatio", "wiki:pose:sexual", "口交（吞）细分", False),
    ("cunnilingus", "wiki:pose:sexual", "舔阴细分", False),
    ("paizuri", "wiki:pose:sexual", "乳交细分", False),
    ("penetration", "wiki:pose:sexual", "插入过程细分", False),
    ("missionary", "wiki:pose:sexual", "传教士体位细分", False),
    ("doggystyle", "wiki:pose:sexual", "后入体位（涩涩）", False),
    ("cowgirl_position", "wiki:pose:sexual", "骑乘位（涩涩）", False),
    ("reverse_cowgirl_position", "wiki:pose:sexual", "反骑乘位（涩涩）", False),
    ("facesitting", "wiki:pose:sexual", "颜坐（涩涩）", False),
    ("fingering", "wiki:pose:sexual", "指交细分", False),
    ("tribadism", "wiki:pose:sexual", "磨镜（涩涩）", False),
    ("prone_bone", "wiki:pose:sexual", "俯卧后入（涩涩）", False),
)

EXTRA_GROUPS = (
    ("wiki:attire:one_piece", "连衣裙与一体式服装", ROOT_GROUP),
    ("wiki:attire:underwear", "内衣", ROOT_GROUP),
    # --- wiki:pose tree (动作与姿势): second root, explicit buckets ---
    ("wiki:pose", "动作与姿势（Danbooru Wiki）", None),
    ("wiki:pose:hand_gestures", "手部动作", "wiki:pose"),
    ("wiki:pose:holding", "持物动作", "wiki:pose"),
    ("wiki:pose:dancing", "舞蹈动作", "wiki:pose"),
    ("wiki:pose:sexual", "性姿势（涩涩）", "wiki:pose"),
)

LABELS_ZH = {
    "headwear and headgear": "帽子与头部装束",
    "shirts and topwear": "上衣与内搭",
    "pants and bottomwear": "裤装与下装",
    "legs and feet": "腿袜与足部",
    "shoes and footwear": "鞋与足部穿着",
    "uniforms and costumes": "制服与角色服",
    "swimsuits and bodysuits": "泳装与紧身连体衣",
    "traditional clothing": "传统服饰",
    "jewelry and accessories": "首饰与配饰",
    "styles and patterns": "风格与图案",
    "head and face": "头部与面部",
    "neck and shoulders": "颈部与肩部",
    "limbs": "四肢配饰",
    "torso and misc": "躯干与其他配饰",
    "patterns": "图案",
    "prints": "印花",
    "types": "类型",
    "styles": "款式",
    "style": "款式",
    "colors": "颜色",
    "colours": "颜色",
    "specific color": "单色",
    "specific colors": "单色",
    "multiple colors": "多色",
    "length": "长度",
    "lengths": "长度",
    "materials": "材质",
    "material": "材质",
    "actions": "动作与状态",
    "action": "动作与状态",
    "actions / states": "动作与状态",
    "actions/positions": "动作与穿着状态",
    "variations": "变化款式",
    "variations/others": "变化与其他款式",
    "other": "其他",
    "others": "其他",
    "misc": "其他",
    "miscellaneous": "其他",
    "main": "主要标签",
    "main tags / legwear by height": "按长度分类",
    "additional detail tags": "外观细节",
    "pattern": "图案",
    "bands": "装饰带",
    "trim": "饰边",
    "neckline": "领口",
    "sleeves": "袖型",
    "fit": "版型",
    "skirt": "裙摆",
    "waistlines": "腰线",
    "types of swimsuits": "泳装类型",
    "male swimwear": "男性泳装",
    "general": "常见风格",
    "decades": "年代风格",
    "japanese fashion styles": "日系时尚",
    "western fashion styles": "西方时尚",
    "chinese & korean fashion styles": "中韩时尚",
    "cultural and historical styles": "文化与历史风格",
    "gyaru": "辣妹系",
    "lolita fashion": "洛丽塔系",
    "dress shoes": "正装鞋",
    "loafers": "乐福鞋",
    "high heels": "高跟鞋",
    "flats": "平底鞋",
    "sandals": "凉鞋",
    "slippers": "拖鞋",
    "sneakers": "运动鞋",
    "clogs": "木屐与洞洞鞋",
    "hats": "帽子",
    "with visor": "带帽舌",
    "with brim": "带帽檐",
    "with ear flaps": "带护耳",
    "brimless": "无檐帽",
    "helmets": "头盔",
    "crowns": "冠冕",
    "non-specific styles": "通用样式",
    "accessories": "配件",
    "gloves": "手套",
    "glove prints": "手套印花",
    "glove accessories": "手套配件",
    "glove trims": "手套饰边",
    "asymmetrical gloves": "不对称手套",
    "glove materials": "手套材质",
    "glove interactions": "手套动作",
    "glove colors": "手套颜色",
    "other handwear": "其他手部穿戴",
    "hand accessories": "手部配饰",
    "rings": "戒指",
    "hand markings": "手部标记",
    "appearance and models of dresses": "连衣裙外观与款式",
    "specific colors of dress": "连衣裙单色",
    "multiple colors of dress": "连衣裙多色",
    "patterns and prints of dress": "连衣裙图案",
    "lengths of dress": "连衣裙长度",
    "other details of the appearance of the dress": "连衣裙外观细节",
    "models of dress as a whole": "连衣裙整体款式",
}

EXCLUDED_HEADINGS = {
    "table of contents", "about", "intro", "examples", "example",
    "non-examples", "non examples", "incorrect examples", "related tags",
    "see also", "see also / external links", "external links", "references",
}

HEADING_RE = re.compile(r"^h([4-6])(?:#[^.\s]+)?\.\s*(.*?)\s*$", re.I)
EXPAND_RE = re.compile(r"^\[expand=(.*?)\]\s*$", re.I)
BULLET_RE = re.compile(r"^(\*+)\s*(.*?)\s*$")
LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
MARKUP_RE = re.compile(r"\[(?:/?(?:b|i|tn|code)|color(?:=[^\]]+)?)\]", re.I)


@dataclass
class Group:
    id: str
    label: str
    parent_id: str | None


@dataclass
class ParsedPage:
    groups: list[Group]
    memberships: set[tuple[str, str]]
    unresolved: set[str]
    skipped: int


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _plain(value: str) -> str:
    value = LINK_RE.sub(lambda match: match.group(1).split("|", 1)[-1] or match.group(1).split("|", 1)[0], value)
    value = MARKUP_RE.sub("", value)
    return " ".join(value.replace("_", " ").split()).strip(" .:-")


def _key(value: str) -> str:
    return unicodedata.normalize("NFKC", _plain(value)).casefold()


def _slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", _plain(value)).encode("ascii", "ignore").decode().casefold()
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value[:56] or hashlib.sha1(value.encode()).hexdigest()[:12]


def _label(value: str) -> str:
    plain = _plain(value)
    return LABELS_ZH.get(plain.casefold(), plain)


def _excluded(value: str, allow_related_tags: bool = False) -> bool:
    key = _key(value)
    if allow_related_tags and key == "related tags":
        # pose/action pages carry their real category links under this heading;
        # treat it as transparent (content merges into the page group).
        return False
    return key in EXCLUDED_HEADINGS or key.startswith(("not ", "false "))


def _link_target(text: str) -> str:
    match = LINK_RE.search(text)
    if not match:
        return ""
    return match.group(1).split("|", 1)[0].strip()


def _resolve_tag(db: sqlite3.Connection, target: str) -> str:
    target = target.strip()
    key = target.replace(" ", "_").casefold()
    if not key or key.startswith(("tag_group:", "tag group:", "list_of_", "list of ")):
        return ""
    row = db.execute(
        "SELECT canonical FROM danbooru_tags WHERE canonical=? AND category=0", (key,)
    ).fetchone()
    if row:
        return row[0]
    rows = db.execute(
        "SELECT t.canonical FROM terms x JOIN tags t ON t.canonical=x.canonical "
        "JOIN danbooru_tags d ON d.canonical=t.canonical "
        "WHERE x.term=? AND d.category=0 ORDER BY coalesce(t.count,0) DESC,t.canonical LIMIT 2",
        (catalog._builder.normalize(target),),
    ).fetchall()
    return rows[0][0] if len(rows) == 1 else ""


def _unique_group_id(base: str, label: str, used: set[str]) -> str:
    candidate = f"{base}:{_slug(label)}"
    if candidate not in used:
        used.add(candidate)
        return candidate
    index = 2
    while f"{candidate}_{index}" in used:
        index += 1
    candidate = f"{candidate}_{index}"
    used.add(candidate)
    return candidate


def parse_page(db: sqlite3.Connection, title: str, body: str, parent_id: str,
               page_label: str, flatten_root: bool = False,
               allow_related_tags: bool = False) -> ParsedPage:
    groups: list[Group] = []
    memberships: set[tuple[str, str]] = set()
    unresolved: set[str] = set()
    used: set[str] = set()
    page_base = ROOT_GROUP if flatten_root else "wiki:section:" + _slug(title.replace(":", "_"))
    page_id = parent_id if flatten_root else f"wiki:page:{_slug(title)}"
    if not flatten_root:
        groups.append(Group(page_id, page_label, parent_id))
        used.add(page_id)

    heading_stack: list[tuple[int, str]] = []
    expand_stack: list[tuple[str, bool]] = []
    bullet_stack: dict[int, tuple[str, str, str]] = {}
    current_parent = page_id
    skipped = 0

    def active_parent() -> str:
        for group_id, valid in reversed(expand_stack):
            if valid:
                return group_id
        return heading_stack[-1][1] if heading_stack else page_id

    for raw_line in body.replace("\r", "").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        heading = HEADING_RE.match(line)
        if heading:
            level, text = int(heading.group(1)), _plain(heading.group(2))
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            expand_stack.clear()
            bullet_stack.clear()
            if _excluded(text, allow_related_tags=allow_related_tags):
                heading_stack.append((level, ""))
                current_parent = ""
                continue
            if allow_related_tags and _key(text) == "related tags":
                # transparent: keep existing group; child headings/bullets attach
                # to the innermost real group so categories land in the page.
                current_parent = next((gid for _, gid in reversed(heading_stack) if gid), page_id)
                heading_stack.append((level, current_parent))
                continue
            parent = next((gid for _, gid in reversed(heading_stack) if gid), page_id)
            group_id = _unique_group_id(page_base, text, used)
            groups.append(Group(group_id, _label(text), parent))
            heading_stack.append((level, group_id))
            current_parent = group_id
            continue
        expand = EXPAND_RE.match(line)
        if expand:
            text = _plain(expand.group(1))
            valid = bool(current_parent) and not _excluded(text, allow_related_tags=allow_related_tags)
            if valid:
                group_id = _unique_group_id(page_base, text, used)
                groups.append(Group(group_id, _label(text), active_parent()))
            else:
                group_id = ""
            expand_stack.append((group_id, valid))
            continue
        if line.casefold() == "[/expand]":
            if expand_stack:
                expand_stack.pop()
            continue
        bullet = BULLET_RE.match(line)
        if not bullet:
            continue
        depth, text = len(bullet.group(1)), bullet.group(2)
        parent = active_parent()
        if not parent or any(not valid for _, valid in expand_stack):
            skipped += 1
            continue
        for old_depth in tuple(bullet_stack):
            if old_depth >= depth:
                bullet_stack.pop(old_depth, None)
        if depth > 1 and depth - 1 in bullet_stack:
            parent_label, parent_tag, parent_group = bullet_stack[depth - 1]
            if not parent_group:
                parent_group = _unique_group_id(page_base, parent_label, used)
                groups.append(Group(parent_group, _label(parent_label), parent))
                if parent_tag:
                    memberships.add((parent_group, parent_tag))
                bullet_stack[depth - 1] = (parent_label, parent_tag, parent_group)
            parent = parent_group
        target = _link_target(text)
        canonical = _resolve_tag(db, target) if target else ""
        label = _plain(target or re.sub(r"!\w+\s+#\d+:?", "", text))
        if canonical:
            memberships.add((parent, canonical))
        elif target and not target.casefold().startswith(("tag group:", "tag_group:")):
            unresolved.add(target)
        bullet_stack[depth] = (label or target, canonical, "")

    return ParsedPage(groups, memberships, unresolved, skipped)


def _load_pages(path: Path) -> dict[str, str]:
    pages: dict[str, str] = {}
    wanted = {title.casefold() for title, _, _, _ in PAGE_SPECS}
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=25_000, columns=["title", "body", "is_deleted"]):
        for row in batch.to_pylist():
            title, body = row.get("title"), row.get("body")
            if (isinstance(title, str) and title.casefold() in wanted
                    and isinstance(body, str) and not row.get("is_deleted")):
                pages[title.casefold()] = body
    missing = sorted(wanted - pages.keys())
    if missing:
        raise ValueError("Wiki pages missing from snapshot: " + ", ".join(missing))
    return pages


def _verify(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"Wiki snapshot is missing: {path}")
    digest = _digest(path)
    rows = pq.ParquetFile(path).metadata.num_rows
    if digest != EXPECTED_SHA256:
        raise ValueError(f"Wiki snapshot SHA256 mismatch: {digest}")
    if rows != EXPECTED_ROWS:
        raise ValueError(f"Wiki snapshot row count mismatch: {rows}")
    return {"size": path.stat().st_size, "sha256": digest, "rows": rows}


def _upsert_group(db: sqlite3.Connection, group: Group):
    db.execute(
        "INSERT INTO groups(id,label_zh,parent_id,source,review_status) VALUES(?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET label_zh=excluded.label_zh,parent_id=excluded.parent_id,"
        "source=excluded.source,review_status=excluded.review_status",
        (group.id, group.label, group.parent_id, SOURCE_ID, "danbooru_wiki_section_snapshot"),
    )


def augment(wiki_path: Path, output_dir: Path) -> dict:
    wiki_path, output_dir = wiki_path.resolve(), output_dir.resolve()
    verified = _verify(wiki_path)
    pages = _load_pages(wiki_path)
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    active_name = manifest.get("database", "")
    if not re.fullmatch(r"catalog-[a-f0-9]{32}\.sqlite3", active_name):
        raise ValueError("invalid active catalog pointer")
    active_database = output_dir / active_name
    if not active_database.is_file():
        raise ValueError("active catalog is missing")

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
            old_ids = [row[0] for row in db.execute("SELECT id FROM groups WHERE source=?", (SOURCE_ID,))]
            if old_ids:
                db.execute("DELETE FROM tag_groups WHERE group_id IN (SELECT id FROM groups WHERE source=?)", (SOURCE_ID,))
                db.execute("DELETE FROM groups WHERE source=?", (SOURCE_ID,))
            db.execute("DELETE FROM tag_sources WHERE source=?", (SOURCE_ID,))

            _upsert_group(db, Group(ROOT_GROUP, ROOT_LABEL, None))
            parsed_pages = []
            known_groups = {ROOT_GROUP}
            for title, parent_id, label, flatten in PAGE_SPECS:
                if parent_id not in known_groups:
                    for gid, glabel, gparent in EXTRA_GROUPS:
                        if gid == parent_id:
                            _upsert_group(db, Group(gid, glabel, gparent))
                            known_groups.add(gid)
                    if parent_id not in known_groups:
                        raise ValueError(f"unknown practical parent for {title}: {parent_id}")
                parsed = parse_page(db, title, pages[title.casefold()], parent_id, label, flatten,
                                   allow_related_tags=str(parent_id).startswith("wiki:pose"))
                for group in parsed.groups:
                    _upsert_group(db, group)
                    known_groups.add(group.id)
                db.executemany("INSERT OR IGNORE INTO tag_groups(group_id,canonical) VALUES(?,?)", parsed.memberships)
                parsed_pages.append(parsed)
            for gid, glabel, gparent in EXTRA_GROUPS:
                if gid not in known_groups:
                    _upsert_group(db, Group(gid, glabel, gparent))

            for parsed in parsed_pages:
                for _, canonical in parsed.memberships:
                    db.execute(
                        "INSERT OR IGNORE INTO tag_sources(canonical,source,payload) VALUES(?,?,?)",
                        (canonical, SOURCE_ID, json.dumps({"role": "wiki_taxonomy_member", "revision": REVISION}, ensure_ascii=True)),
                    )
            catalog._builder._finish_groups(db)
            db.execute("UPDATE metadata SET value=? WHERE key='version'", (version,))
            counts = catalog._builder._counts(db)
            if counts["tag_count"] < int(manifest.get("counts", {}).get("tag_count", 0)):
                raise ValueError("Wiki taxonomy update would reduce tag coverage")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Wiki taxonomy catalog failed quick_check")
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Wiki taxonomy catalog failed foreign_key_check")
            db.execute("ANALYZE")
            db.commit()
        finally:
            db.close()

        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        group_count = sum(len(item.groups) for item in parsed_pages) + 1 + len(EXTRA_GROUPS)
        membership_count = len(set().union(*(item.memberships for item in parsed_pages)))
        unresolved = sorted(set().union(*(item.unresolved for item in parsed_pages)))
        source = {
            "id": SOURCE_ID,
            "path": f"SAkizuki/danbooru_wikis_2026@{REVISION}/{WIKI_FILE}",
            "location": "external_verified_cache",
            "snapshot_verified": True,
            "revision": REVISION,
            "license": "MIT",
            "upstream": UPSTREAM,
            "file": verified,
            "pages_selected": len(PAGE_SPECS),
            "groups_created": group_count,
            "memberships_parsed": membership_count,
            "unresolved_links": len(unresolved),
            "unresolved_sample": unresolved[:100],
            "skipped_lines": sum(item.skipped for item in parsed_pages),
            "classification_policy": "human-maintained page set plus source Wiki section hierarchy",
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
        coverage["wiki_taxonomy"] = (
            "Practical attire navigation from a pinned Danbooru Wiki snapshot; maintained page set, "
            "Wiki section hierarchy, multi-label membership, and general-tag validation"
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
    parser.add_argument("wiki_path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "resources/danbooru_library")
    args = parser.parse_args()
    try:
        result = augment(args.wiki_path, args.output_dir)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        parser.exit(1, f"Wiki taxonomy augmentation failed; active manifest preserved: {exc}\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
