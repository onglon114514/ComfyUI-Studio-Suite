from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path


SCHEMA_VERSION = "2"


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\\(", "(").replace("\\)", ")").replace("_", " ")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _primary_display_name(value: str) -> str:
    text = str(value or "").strip()
    return re.split(r"\s+[–—-]\s+", text, maxsplit=1)[0].strip()


class LocalDanbooruDictionary:
    """Small in-memory overrides plus an optional disk-backed character index."""

    def __init__(self, package_dir: Path, storage_dir: Path):
        self.resources_dir = package_dir / "resources"
        self.manual_path = self.resources_dir / "danbooru_character_aliases.json"
        self.source_path = self.resources_dir / "danbooru_character_webui.normalized.jsonl"
        self.cache_dir = storage_dir / "cache"
        self.index_path = self.cache_dir / "danbooru_character_aliases.sqlite3"
        self._manual_signature: tuple[int, int] | None = None
        self._manual: dict[str, dict] = {}

    def _load_manual(self) -> dict[str, dict]:
        if not self.manual_path.exists():
            self._manual = {}
            self._manual_signature = None
            return self._manual
        stat = self.manual_path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._manual_signature:
            return self._manual
        payload = json.loads(self.manual_path.read_text(encoding="utf-8-sig"))
        result: dict[str, dict] = {}
        if isinstance(payload, dict):
            for canonical, raw in payload.items():
                if not isinstance(raw, dict):
                    continue
                aliases = [str(alias).strip() for alias in [canonical, *list(raw.get("aliases", []) or [])] if str(alias).strip()]
                display_zh = str(raw.get("display_zh", "") or "").strip()
                if not display_zh:
                    display_zh = next((alias for alias in aliases if re.search(r"[\u3400-\u9fff]", alias)), "")
                entry = {
                    "canonical": str(canonical).strip(),
                    "display_zh": display_zh,
                    "copyright": str(raw.get("copyright", "") or "").strip(),
                    "trigger_tags": str(raw.get("trigger_tags", "") or "").strip(),
                    "core_tags": str(raw.get("core_tags", "") or "").strip(),
                    "source": "builtin",
                }
                for alias in aliases:
                    key = _normalize(alias)
                    if key:
                        result[key] = entry
        self._manual = result
        self._manual_signature = signature
        return result

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.index_path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _index_metadata(self) -> dict[str, str]:
        if not self.index_path.exists():
            return {}
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute("SELECT key, value FROM metadata").fetchall()
            return {str(row["key"]): str(row["value"]) for row in rows}
        except Exception:
            return {}

    def status(self) -> dict:
        manual_count = len(self._load_manual())
        metadata = self._index_metadata()
        source_signature = ""
        if self.source_path.exists():
            stat = self.source_path.stat()
            source_signature = f"{stat.st_size}:{stat.st_mtime_ns}"
        ready = bool(
            metadata.get("schema_version") == SCHEMA_VERSION
            and metadata.get("source_signature") == source_signature
        )
        return {
            "ready": ready,
            "mode": "indexed" if ready else "builtin_only",
            "manual_aliases": manual_count,
            "indexed_aliases": int(metadata.get("alias_count", "0") or 0),
            "source_available": self.source_path.exists(),
            "index_path": str(self.index_path),
        }

    def lookup_many(self, terms: list[str]) -> dict[str, dict]:
        manual = self._load_manual()
        normalized = {_normalize(term): term for term in terms if _normalize(term)}
        found = {key: manual[key] for key in normalized if key in manual}
        missing = [key for key in normalized if key not in found]
        if missing and self.status()["ready"]:
            try:
                with closing(self._connect()) as connection:
                    for start in range(0, len(missing), 300):
                        chunk = missing[start : start + 300]
                        placeholders = ",".join("?" for _ in chunk)
                        rows = connection.execute(
                            f"SELECT alias, canonical, display_zh, copyright, trigger_tags, core_tags "
                            f"FROM aliases WHERE alias IN ({placeholders})",
                            chunk,
                        ).fetchall()
                        for row in rows:
                            found[str(row["alias"])] = {
                                "canonical": str(row["canonical"]),
                                "display_zh": str(row["display_zh"] or ""),
                                "copyright": str(row["copyright"] or ""),
                                "trigger_tags": str(row["trigger_tags"] or ""),
                                "core_tags": str(row["core_tags"] or ""),
                                "source": "indexed",
                            }
            except Exception:
                pass
        return found

    def protect_text(self, text: str, direction: str) -> tuple[str, list[dict]]:
        source = str(text or "")
        if not source.strip():
            return source, []

        # Prompt inputs are normally comma/newline separated. Chinese possessive
        # forms are included so "作品的角色" can still resolve to a character alias.
        candidates: list[tuple[int, int, str]] = []
        for match in re.finditer(r"[^,，;；\n]+", source):
            raw = match.group(0)
            stripped = raw.strip()
            offset = match.start() + len(raw) - len(raw.lstrip())
            if stripped:
                candidates.append((offset, offset + len(stripped), stripped))
            for part in re.finditer(r"[\u3400-\u9fffA-Za-z0-9_()\\ -]{2,}", stripped):
                value = part.group(0).strip()
                if value and value != stripped:
                    start = offset + part.start() + len(part.group(0)) - len(part.group(0).lstrip())
                    candidates.append((start, start + len(value), value))
                for sub in re.finditer(r"[^的\s]{2,}", value):
                    sub_value = sub.group(0).strip()
                    if sub_value and sub_value != value:
                        start = offset + part.start() + sub.start()
                        candidates.append((start, start + len(sub_value), sub_value))

            # Character names are often embedded in a short Chinese sentence.
            # Bounded substrings enable exact disk-index lookups without loading
            # the complete alias table into process memory.
            for cjk in re.finditer(r"[\u3400-\u9fff]{2,24}", stripped):
                value = cjk.group(0)
                for length in range(min(12, len(value)), 1, -1):
                    for index in range(0, len(value) - length + 1):
                        start = offset + cjk.start() + index
                        candidates.append((start, start + length, value[index : index + length]))
                        if len(candidates) >= 800:
                            break
                    if len(candidates) >= 800:
                        break
                if len(candidates) >= 800:
                    break

        candidates = list(dict.fromkeys(candidates))

        lookup = self.lookup_many([item[2] for item in candidates])
        matches: list[dict] = []
        occupied: list[tuple[int, int]] = []
        for start, end, raw in sorted(candidates, key=lambda item: (-(item[1] - item[0]), item[0])):
            entry = lookup.get(_normalize(raw))
            if not entry or any(start < other_end and end > other_start for other_start, other_end in occupied):
                continue
            canonical = str(entry.get("canonical", "") or "").strip()
            display_zh = str(entry.get("display_zh", "") or "").strip()
            replacement = canonical if direction.startswith("zh_to_en") else (display_zh or raw)
            if not replacement or _normalize(replacement) == _normalize(raw):
                continue
            occupied.append((start, end))
            matches.append(
                {
                    "start": start,
                    "end": end,
                    "source": raw,
                    "translated": replacement,
                    "canonical": canonical,
                    "display_zh": display_zh,
                    "copyright": entry.get("copyright", ""),
                    "source_kind": entry.get("source", ""),
                }
            )

        protected = source
        for item in sorted(matches, key=lambda value: value["start"], reverse=True):
            protected = protected[: item["start"]] + item["translated"] + protected[item["end"] :]
        return protected, sorted(matches, key=lambda value: value["start"])

    def build_index(self) -> dict:
        if not self.source_path.exists():
            raise FileNotFoundError(f"dictionary source not found: {self.source_path}")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_suffix(".sqlite3.tmp")
        temporary.unlink(missing_ok=True)
        source_stat = self.source_path.stat()
        source_signature = f"{source_stat.st_size}:{source_stat.st_mtime_ns}"
        alias_count = 0
        record_count = 0

        with closing(sqlite3.connect(str(temporary))) as connection:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute(
                "CREATE TABLE aliases ("
                "alias TEXT PRIMARY KEY, canonical TEXT NOT NULL, display_zh TEXT, copyright TEXT, "
                "trigger_tags TEXT, core_tags TEXT, post_count INTEGER NOT NULL DEFAULT 0)"
            )
            connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            batch: list[tuple] = []
            with self.source_path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(row, dict):
                        continue
                    canonical = str(row.get("character角色", "") or "").strip()
                    if not canonical:
                        continue
                    display_zh = str(row.get("character角色_2", "") or "").strip()
                    copyright_text = str(row.get("copyright作品", "") or "").strip()
                    trigger_tags = str(row.get("trigger触发tag_2", row.get("trigger触发tag", "")) or "").strip()
                    core_tags = str(row.get("core_tags_2", row.get("core_tags", "")) or "").strip()
                    post_count = int(row.get("count", 0) or 0)
                    aliases = {
                        canonical,
                        canonical.replace("_", " "),
                    }
                    primary_display = _primary_display_name(display_zh)
                    # The source dataset also contains translated generic names
                    # such as "girl" and "ark". Only contextualized Chinese
                    # labels ("name - work/details") are safe automatic aliases.
                    if display_zh and primary_display and primary_display != display_zh:
                        aliases.add(display_zh)
                        aliases.add(primary_display)
                    for alias in aliases:
                        normalized = _normalize(alias)
                        if normalized:
                            batch.append(
                                (normalized, canonical, display_zh, copyright_text, trigger_tags, core_tags, post_count)
                            )
                    record_count += 1
                    if len(batch) >= 5000:
                        self._write_batch(connection, batch)
                        alias_count += len(batch)
                        batch.clear()
            if batch:
                self._write_batch(connection, batch)
                alias_count += len(batch)
            actual_alias_count = int(connection.execute("SELECT COUNT(*) FROM aliases").fetchone()[0])
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "source_signature": source_signature,
                "source_path": str(self.source_path),
                "record_count": str(record_count),
                "alias_count": str(actual_alias_count),
            }
            connection.executemany("INSERT INTO metadata(key, value) VALUES(?, ?)", metadata.items())
            connection.commit()

        temporary.replace(self.index_path)
        return {
            "records": record_count,
            "aliases_seen": alias_count,
            "aliases_indexed": actual_alias_count,
            "index_path": str(self.index_path),
        }

    @staticmethod
    def _write_batch(connection: sqlite3.Connection, rows: list[tuple]) -> None:
        connection.executemany(
            "INSERT INTO aliases(alias, canonical, display_zh, copyright, trigger_tags, core_tags, post_count) "
            "VALUES(?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(alias) DO UPDATE SET "
            "canonical=excluded.canonical, display_zh=excluded.display_zh, copyright=excluded.copyright, "
            "trigger_tags=excluded.trigger_tags, core_tags=excluded.core_tags, post_count=excluded.post_count "
            "WHERE excluded.post_count > aliases.post_count",
            rows,
        )
