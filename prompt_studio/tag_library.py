"""Read-only, offline public dictionary. Construction never builds an index.

Search is a normalized prefix search, ordered by canonical name. Cursors are
bound to the version and filters; callers must restart after a version change.
Counts are source-scoped, not a claim about current Danbooru post counts.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path


SCHEMA_VERSION = 1
KINDS = {"unknown", "general", "artist", "copyright", "character", "meta"}
MAX_LIMIT = 100
MAX_PAGE = 100000
MAX_LOOKUP_TERMS = 512
MAX_LOOKUP_TEXT = 8192
MAX_LOOKUP_TOTAL = 120000


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\\(", "(").replace("\\)", ")").replace("_", " ")
    return " ".join(value.split()).casefold()


def _string(value, name: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{name} must be a string of at most {maximum} characters")
    return value.strip()


def _limit(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("limit must be a positive integer")
    return min(value, MAX_LIMIT)


def _page(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > MAX_PAGE:
        raise ValueError(f"page must be an integer from 1 to {MAX_PAGE}")
    return value


def _sort(value) -> str:
    value = _string(value, "sort", 16) or "name"
    if value not in {"name", "popular"}:
        raise ValueError("sort must be name or popular")
    return value


def _initial(value) -> str:
    value = _string(value, "initial", 3).casefold()
    if value and value != "0-9" and not re.fullmatch(r"[a-z]", value):
        raise ValueError("initial must be A-Z or 0-9")
    return value


def _prefix_end(value: str) -> str:
    for index in range(len(value) - 1, -1, -1):
        if ord(value[index]) < 0x10FFFF:
            return value[:index] + chr(ord(value[index]) + 1)
    raise ValueError("invalid search prefix")


class TagLibrary:
    def __init__(self, package_dir: Path):
        self.library_dir = Path(package_dir) / "resources" / "danbooru_library"

    @contextmanager
    def _snapshot(self):
        connection = None
        manifest = {"version": None, "counts": {}, "sources": []}
        error = "library_not_installed"
        try:
            path = self.library_dir / "manifest.json"
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("manifest is too large")
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported library schema")
            filename = manifest.get("database", "")
            if not isinstance(filename, str) or not re.fullmatch(r"catalog-[a-f0-9]{32}\.sqlite3", filename):
                raise ValueError("invalid library database pointer")
            database = (self.library_dir / filename).resolve()
            if database.parent != self.library_dir.resolve():
                raise ValueError("database must stay inside the library directory")
            connection = sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA temp_store=MEMORY")
            version = connection.execute("SELECT value FROM metadata WHERE key='version'").fetchone()
            schema = connection.execute("PRAGMA user_version").fetchone()[0]
            if not version or version[0] != manifest.get("version") or schema != SCHEMA_VERSION:
                raise ValueError("manifest/database version mismatch")
            error = ""
        except (OSError, UnicodeError, ValueError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            connection = None
            manifest = {"version": None, "counts": {}, "sources": []}
            error = "library_not_installed" if isinstance(exc, FileNotFoundError) else "library_unavailable"
        try:
            yield connection, manifest, error
        finally:
            if connection is not None:
                connection.close()

    def status(self) -> dict:
        with self._snapshot() as (db, manifest, error):
            result = dict(manifest, ready=db is not None)
            if error:
                result["reason"] = error
            return result

    def categories(self) -> dict:
        with self._snapshot() as (db, _, __):
            if db is None:
                return {"items": []}
            raw = [dict(row) for row in db.execute(
                "SELECT id,label_zh,parent_id,count,source,review_status FROM groups"
            )]
            by_id = {row["id"]: row for row in raw}

            def ancestry(row):
                chain, seen = [], set()
                current = row
                while current:
                    group_id = current["id"]
                    if group_id in seen:
                        break
                    seen.add(group_id)
                    chain.append(current)
                    current = by_id.get(current.get("parent_id"))
                chain.reverse()
                return chain

            items = []
            for row in raw:
                chain = ancestry(row)
                item = dict(row)
                item.update(
                    depth=max(0, len(chain) - 1),
                    root_id=chain[0]["id"] if chain else row["id"],
                    path=" / ".join(part["label_zh"] or part["id"] for part in chain),
                )
                items.append(item)
            items.sort(key=lambda row: (
                0 if row["root_id"] == "wiki:attire" else 1,
                row["path"].casefold(), row["id"],
            ))
            kinds = db.execute("SELECT kind,count(*) AS count FROM tags GROUP BY kind ORDER BY kind")
            return {"items": items, "kinds": [dict(row) for row in kinds]}

    def search(self, q='', category='', kind='', cursor='', limit=40, page=1,
               sort='name', initial='', work='') -> dict:
        q = normalize(_string(q, "q"))
        category = _string(category, "category")
        kind = _string(kind, "kind")
        cursor = _string(cursor, "cursor", 4096)
        work = normalize(_string(work, "work"))
        limit = _limit(limit)
        page = _page(page)
        sort = _sort(sort)
        initial = _initial(initial)
        if kind and kind not in KINDS:
            raise ValueError("unknown kind")
        if cursor and sort != "name":
            raise ValueError("cursor pagination only supports name sorting")
        filters = hashlib.sha256(
            json.dumps([q, category, kind, sort, initial, work], ensure_ascii=True).encode()
        ).hexdigest()
        with self._snapshot() as (db, manifest, _):
            if db is None:
                return {"items": [], "next_cursor": ""}
            last = ""
            cursor_page = page
            if cursor:
                try:
                    state = json.loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
                    if (state["version"] != manifest["version"] or state["filters"] != filters):
                        raise ValueError("cursor version or filters changed; restart pagination")
                    last = _string(state["last"], "cursor.last")
                    cursor_page = _page(int(state.get("page", page)))
                except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                    raise ValueError("invalid or expired cursor; restart pagination") from exc
            where, params = [], []
            if q:
                where.append("t.canonical IN (SELECT canonical FROM terms WHERE term>=? AND term<?)")
                params.extend([q, _prefix_end(q)])
            if category:
                where.append("t.canonical IN (SELECT canonical FROM tag_groups WHERE group_id=?)")
                params.append(category)
            if kind:
                where.append("t.kind=?")
                params.append(kind)
            if initial == "0-9":
                where.append("t.canonical>=? AND t.canonical<?")
                params.extend(["0", ":"])
            elif initial:
                where.append("t.canonical>=? AND t.canonical<?")
                params.extend([initial, chr(ord(initial) + 1)])
            if work:
                has_works = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='works'"
                ).fetchone()
                if not has_works:
                    return {"items": [], "next_cursor": "", "total_count": 0,
                            "total_pages": 1, "page": 1, "page_size": limit, "sort": sort}
                where.append(
                    "t.canonical IN (SELECT tw.canonical FROM tag_works tw "
                    "JOIN works w ON w.name=tw.work WHERE w.term>=? AND w.term<?)"
                )
                params.extend([work, _prefix_end(work)])
            predicate = " AND ".join(where) if where else "1"
            total_count = int(db.execute(
                "SELECT count(*) FROM tags t WHERE " + predicate, params
            ).fetchone()[0])
            total_pages = max(1, (total_count + limit - 1) // limit)
            if cursor:
                page = min(cursor_page, total_pages)
                row_where = f"({predicate}) AND t.canonical>?"
                row_params = [*params, last, limit + 1]
                order = "t.canonical"
                rows = db.execute(
                    "SELECT t.canonical,t.label_zh,t.kind,t.count,t.count_source,t.count_scope "
                    "FROM tags t WHERE " + row_where + f" ORDER BY {order} LIMIT ?", row_params
                ).fetchall()
            else:
                page = min(page, total_pages)
                order = "t.count DESC,t.canonical" if sort == "popular" else "t.canonical"
                rows = db.execute(
                    "SELECT t.canonical,t.label_zh,t.kind,t.count,t.count_source,t.count_scope "
                    "FROM tags t WHERE " + predicate + f" ORDER BY {order} LIMIT ? OFFSET ?",
                    [*params, limit + 1, (page - 1) * limit],
                ).fetchall()
            items = [dict(row) for row in rows[:limit]]
            next_cursor = ""
            if sort == "name" and len(rows) > limit:
                state = {"version": manifest["version"], "filters": filters,
                         "last": items[-1]["canonical"], "page": page + 1}
                next_cursor = base64.urlsafe_b64encode(json.dumps(state, ensure_ascii=True).encode()).decode()
            return {"items": items, "next_cursor": next_cursor, "total_count": total_count,
                    "total_pages": total_pages, "page": page, "page_size": limit, "sort": sort}

    def works(self, q='', limit=50) -> dict:
        q = normalize(_string(q, "q"))
        limit = _limit(limit)
        with self._snapshot() as (db, _, __):
            if db is None or not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='works'").fetchone():
                return {"items": []}
            where, params = "1", []
            if q:
                where, params = "term>=? AND term<?", [q, _prefix_end(q)]
            rows = db.execute(
                "SELECT name,label_zh,post_count,character_count FROM works WHERE " + where +
                " ORDER BY character_count DESC,post_count DESC,name LIMIT ?", [*params, limit]
            )
            return {"items": [dict(row) for row in rows]}

    def detail(self, canonical) -> dict:
        canonical = _string(canonical, "canonical")
        with self._snapshot() as (db, _, __):
            if db is None:
                return {}
            row = db.execute("SELECT canonical,label_zh,kind,count,count_source,count_scope,"
                             "description,label_source FROM tags WHERE canonical=?", (canonical,)).fetchone()
            if row is None:
                return {}
            result = dict(row)
            aliases = db.execute("SELECT DISTINCT text FROM terms WHERE canonical=? AND role='alias' "
                                 "ORDER BY text LIMIT 41", (canonical,)).fetchall()
            result.update(aliases=[r[0] for r in aliases[:40]], aliases_truncated=len(aliases) > 40,
                          official_category=None, label_review_status="source_unverified")
            result["groups"] = [dict(r) for r in db.execute(
                "SELECT g.id,g.label_zh,g.parent_id,g.source,g.review_status FROM groups g "
                "JOIN tag_groups m ON m.group_id=g.id WHERE m.canonical=? ORDER BY g.id", (canonical,))]
            result["sources"] = [{"id": r[0], **json.loads(r[1])} for r in db.execute(
                "SELECT source,payload FROM tag_sources WHERE canonical=? ORDER BY source", (canonical,))]
            return result

    def related(self, canonical, limit=20) -> dict:
        canonical = _string(canonical, "canonical")
        limit = _limit(limit)
        with self._snapshot() as (db, _, __):
            if db is None:
                return {"items": []}
            # Each indexed direction contributes its true top N, not file-order samples.
            rows = db.execute("WITH candidates AS ("
                              "SELECT * FROM (SELECT b AS canonical,count FROM edges WHERE a=? "
                              "ORDER BY count DESC,b LIMIT ?) UNION ALL "
                              "SELECT * FROM (SELECT a AS canonical,count FROM edges WHERE b=? "
                              "ORDER BY count DESC,a LIMIT ?)) "
                              "SELECT t.canonical,t.label_zh,t.kind,c.count,'cooccurrence' AS source,"
                              "'cooccurrence' AS count_source,'source_snapshot_unverified' AS count_scope "
                              "FROM candidates c JOIN tags t ON t.canonical=c.canonical "
                              "ORDER BY c.count DESC,t.canonical LIMIT ?",
                              (canonical, limit, canonical, limit, limit))
            return {"items": [dict(r) for r in rows]}

    def lookup(self, terms, language='zh') -> dict:
        if language not in {"zh", "en"}:
            raise ValueError("language must be zh or en")
        if not isinstance(terms, (list, tuple)) or len(terms) > MAX_LOOKUP_TERMS:
            raise ValueError("terms must contain at most 512 strings")
        for term in terms:
            _string(term, "term", MAX_LOOKUP_TEXT)
        if sum(len(term) for term in terms) > MAX_LOOKUP_TOTAL:
            raise ValueError("lookup text must total at most 120000 characters")
        result = {"matches": [], "ambiguous": [], "unmatched": [], "available_entries": 0, "ready": False}
        with self._snapshot() as (db, manifest, _):
            if db is None:
                result["unmatched"] = list(terms)
                return result
            result.update(ready=True, available_entries=manifest["counts"].get("tag_count", 0))
            for text in terms:
                if len(text) > 512:
                    result["unmatched"].append(text)
                    continue
                rows = db.execute("SELECT t.canonical,t.label_zh,t.label_source FROM tags t WHERE "
                                  "t.canonical IN (SELECT canonical FROM terms WHERE term=?) "
                                  "ORDER BY t.canonical LIMIT 2", (normalize(text),)).fetchall()
                if len(rows) > 1:
                    result["ambiguous"].append(text)
                elif rows and (language == "en" or re.search(r"[\u3400-\u9fff]", rows[0]["label_zh"] or "")):
                    row = rows[0]
                    source = row["label_source"]
                    if language == "en":
                        source = db.execute("SELECT source FROM terms WHERE term=? AND canonical=? "
                                            "ORDER BY source LIMIT 1", (normalize(text), row["canonical"])).fetchone()[0]
                    result["matches"].append({"text": text, "local": row["label_zh"] if language == "zh"
                                              else row["canonical"], "source": source, "canonical": row["canonical"]})
                else:
                    result["unmatched"].append(text)
        return result
