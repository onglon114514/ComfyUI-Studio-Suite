from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict

TRANSLATION_CACHE_LIMIT = 512


def build_translation_prompt(text: str, direction: str) -> str:
    target = "英语" if str(direction).startswith("zh_to_en") else "中文"
    if str(direction).endswith("_tags"):
        return (
            f"将标签翻译为{target}，按原顺序用英文逗号分隔，只输出翻译结果：\n"
            f"{str(text or '').strip()}"
        )
    return (
        f"将以下文本翻译为{target}，注意只需要输出翻译后的结果，不要额外解释。\n\n"
        f"{str(text or '').strip()}"
    )


def clean_translation_output(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text


def translation_needed(text: str, direction: str) -> bool:
    source = str(text or "").strip()
    if not source:
        return False
    if str(direction).startswith("zh_to_en"):
        return bool(re.search(r"[\u3400-\u9fff]", source))
    return bool(re.search(r"[A-Za-z]", source))


_ANGLE_TAG_PREFIX = re.compile(r"<(?:lora|lyco|wlr):", re.IGNORECASE)


def _angle_tag_end(source: str, index: int) -> int | None:
    """Return the closing ``>`` index when a LoRA-style tag starts at ``index``."""
    if index >= len(source) or source[index] != "<":
        return None
    if not _ANGLE_TAG_PREFIX.match(source, index):
        return None
    close = source.find(">", index + 1)
    return close if close != -1 else None


def split_tag_parts(text: str) -> list[str]:
    """Split only top-level tag separators, preserving weighted expressions."""
    source = str(text or "").strip()
    if not source:
        return []
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    escaped = False
    opening = {"(", "[", "{", "（"}
    closing = {")", "]", "}", "）"}
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        close = _angle_tag_end(source, index)
        if close is not None:
            current.append(source[index:close + 1])
            index = close + 1
            continue
        if escaped:
            current.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            index += 1
            continue
        if char in opening:
            depth += 1
        elif char in closing and depth > 0:
            depth -= 1
        if depth == 0 and char in {",", "，", ";", "；", "\n", "\r"}:
            item = "".join(current).strip()
            if item:
                parts.append(item)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts or [source]


TAG_SEPARATORS = set(",，;；\n\r")
SENTENCE_ENDINGS = set("。！？!?；;")


def split_translation_units(text: str, direction: str) -> list[tuple[str, str]]:
    """Split text into (content, separator) pairs without losing punctuation.

    ``_tags`` splits on commas/semicolons/newlines. Natural-language text splits on
    sentence endings; comma-separated tag lists without any sentence ending fall
    back to tag splitting so mixed lines such as ``1girl, 长发`` keep English tags.
    """
    source = str(text or "")
    if not source.strip():
        return []
    if str(direction).endswith("_tags"):
        return _split_units(source, "tags")
    if _has_sentence_ending(source):
        return _split_units(source, "text")
    return _split_units(source, "tags")


def _dot_is_sentence_end(source: str, index: int) -> bool:
    if source[index] != ".":
        return False
    if index > 0 and (source[index - 1].isdigit() or source[index - 1] == "."):
        return False
    if index + 1 >= len(source):
        return True
    following = source[index + 1]
    return following in " \t\n\r" or "\u3400" <= following <= "\u9fff"


def _has_sentence_ending(source: str) -> bool:
    depth = 0
    escaped = False
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        close = _angle_tag_end(source, index)
        if close is not None:
            index = close + 1
            continue
        if char in "([{（":
            depth += 1
            index += 1
            continue
        if char in ")]}）" and depth > 0:
            depth -= 1
            index += 1
            continue
        if depth > 0:
            index += 1
            continue
        if char in SENTENCE_ENDINGS or _dot_is_sentence_end(source, index):
            return True
        index += 1
    return False


def _split_units(source: str, mode: str) -> list[tuple[str, str]]:
    units: list[tuple[str, str]] = []
    current: list[str] = []
    depth = 0
    escaped = False
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        close = _angle_tag_end(source, index)
        if close is not None:
            current.append(source[index:close + 1])
            index = close + 1
            continue
        if escaped:
            current.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            index += 1
            continue
        if char in "([{（":
            depth += 1
        elif char in ")]}）" and depth > 0:
            depth -= 1

        split_here = False
        if depth == 0:
            if mode == "tags":
                split_here = char in TAG_SEPARATORS
            else:
                split_here = char in SENTENCE_ENDINGS or _dot_is_sentence_end(source, index)
        if split_here:
            end = index + 1
            while end < length and source[end] in " \t\r\n":
                end += 1
            content = "".join(current).strip()
            units.append((content, source[index:end]))
            current = []
            index = end
            continue
        current.append(char)
        index += 1

    tail = "".join(current)
    if tail.strip():
        units.append((tail.strip(), ""))
    elif units and tail:
        content, separator = units[-1]
        units[-1] = (content, separator + tail)
    return units


def _localize_separator(separator: str, direction: str) -> str:
    if not separator:
        return separator
    if str(direction).startswith("zh_to_en"):
        mapped = separator.translate(str.maketrans({"。": ".", "！": "!", "？": "?", "；": ";"}))
        if mapped[0] in ".!?;":
            rest = mapped[1:].lstrip(" \t")
            if rest.startswith(("\n", "\r")):
                return mapped[0] + rest
            return mapped[0] + " " + rest
        return mapped
    mapped = separator.translate(str.maketrans({".": "。", "!": "！", "?": "？", ";": "；"}))
    if mapped[0] in "。！？；":
        return mapped[0] + mapped[1:].lstrip(" \t")
    return mapped


def _strip_sentence_punctuation(text: str, separator: str) -> str:
    if separator and (separator[0] in SENTENCE_ENDINGS or separator[0] == "."):
        return str(text or "").rstrip(" \t。！？!?；;.").rstrip()
    return str(text or "")


def normalize_tag_weights(text: str) -> str:
    """Collapse ``(tag: 1.1)`` to ``(tag:1.1)`` without touching other content."""
    return re.sub(r":\s+(?=[0-9]*\.?[0-9]+\))", ":", str(text or ""))


def normalize_tag_separators(text: str) -> str:
    return ", ".join(split_tag_parts(normalize_tag_weights(text)))


class TranslationCache:
    """Small LRU cache shared by every translation backend."""

    def __init__(self, limit: int = TRANSLATION_CACHE_LIMIT, lock: threading.RLock | None = None):
        self._items: OrderedDict[tuple[str, str, str], str] = OrderedDict()
        self.limit = max(1, int(limit))
        self.lock = lock or threading.RLock()

    def get(self, key: tuple[str, str, str]) -> str | None:
        with self.lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(self, key: tuple[str, str, str], value: str) -> None:
        with self.lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.limit:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self.lock:
            self._items.clear()

    def __len__(self) -> int:
        with self.lock:
            return len(self._items)


class TagTranslator:
    """Backend-agnostic translation flow: language skip, cache, tag split, retry.

    ``complete(prompt, max_tokens, temperature)`` must return
    ``(translated_text, backend_timings)``; the backend owns model loading.
    """

    def __init__(self, complete, cache: TranslationCache | None = None):
        self.complete = complete
        self.cache = cache if cache is not None else TranslationCache()

    def translate(
        self,
        text: str,
        direction: str,
        max_tokens: int = 256,
        temperature: float = 0.05,
        cache_namespace: str = "",
    ) -> dict:
        started = time.monotonic()
        source = str(text or "").strip()
        is_tags = str(direction).endswith("_tags")
        namespace = str(cache_namespace or "")
        normalized_direction = str(direction or "").strip().lower()

        if is_tags:
            if not translation_needed(source, direction):
                return self._result(
                    normalize_tag_separators(source), started, {}, skipped=True
                )
            cache_key = (namespace, normalized_direction, source)
            cached = self.cache.get(cache_key)
            if cached is not None:
                return self._result(cached, started, {}, cache_hit=True)

            tag_parts = split_tag_parts(source)
            pending_indices = [
                index for index, part in enumerate(tag_parts)
                if translation_needed(part, direction)
            ]
            request_text = ", ".join(tag_parts[index] for index in pending_indices)

            translated, backend_timings = self.complete(
                build_translation_prompt(request_text, direction),
                max_tokens,
                temperature,
            )
            translated = clean_translation_output(translated)
            translated_parts = [normalize_tag_weights(part) for part in split_tag_parts(translated)]
            fallback_requests = 0
            if len(translated_parts) != len(pending_indices):
                translated_parts = []
                for index in pending_indices:
                    item, _ = self.complete(
                        build_translation_prompt(tag_parts[index], direction),
                        max(16, min(max_tokens, 64)),
                        temperature,
                    )
                    translated_parts.append(normalize_tag_separators(clean_translation_output(item)))
                    fallback_requests += 1
            combined = list(tag_parts)
            for index, translated_part in zip(pending_indices, translated_parts):
                combined[index] = translated_part
            translated = ", ".join(combined)
            backend_timings = dict(backend_timings or {})
            backend_timings["tag_items"] = len(tag_parts)
            backend_timings["translated_tag_items"] = len(pending_indices)
            backend_timings["fallback_requests"] = fallback_requests
            self.cache.put(cache_key, translated)
            return self._result(translated, started, backend_timings)

        units = split_translation_units(source, direction)
        pending = [
            index for index, (content, _separator) in enumerate(units)
            if translation_needed(content, direction)
        ]
        if not pending:
            return self._result(source, started, {}, skipped=True)

        rendered: list[str] = []
        backend_timings: dict = {}
        calls = 0
        cache_hits = 0
        for index, (content, separator) in enumerate(units):
            if index not in pending or not content:
                rendered.append(content + separator)
                continue
            cache_key = (namespace, normalized_direction, content)
            cached = self.cache.get(cache_key)
            if cached is None:
                raw, unit_timings = self.complete(
                    build_translation_prompt(content, direction),
                    max_tokens,
                    temperature,
                )
                cached = _strip_sentence_punctuation(
                    clean_translation_output(raw), separator
                )
                self.cache.put(cache_key, cached)
                calls += 1
                if isinstance(unit_timings, dict):
                    for key in ("prompt_n", "predicted_n"):
                        if key in unit_timings:
                            backend_timings[key] = backend_timings.get(key, 0) + int(unit_timings[key] or 0)
            else:
                cache_hits += 1
            rendered.append(cached + _localize_separator(separator, direction))
        backend_timings["text_units"] = len(units)
        backend_timings["translated_text_units"] = len(pending)
        backend_timings["cache_hits"] = cache_hits
        translated = "".join(rendered)
        if str(direction).startswith("zh_to_en"):
            translated = translated.rstrip(" 	")
        return self._result(
            translated,
            started,
            backend_timings,
            cache_hit=calls == 0,
        )

    @staticmethod
    def _result(
        translated: str,
        started: float,
        backend_timings: dict,
        *,
        cache_hit: bool = False,
        skipped: bool = False,
    ) -> dict:
        return {
            "translated_text": translated,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "backend_timings": backend_timings,
            "cache_hit": cache_hit,
            "skipped_already_target_language": skipped,
        }
