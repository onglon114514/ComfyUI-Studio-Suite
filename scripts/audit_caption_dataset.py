"""Audit a caption dataset folder before training.

Usage:
    python -B scripts/audit_caption_dataset.py <dir> [<dir> ...] [--trigger @name] [--min-words 60] [--max-words 260]

Checks every .txt caption next to an image and reports:
  - empty captions and orphan captions (no matching image)
  - missing trigger prefix
  - word-count outliers
  - style / medium words that leak into style-LoRA captions
  - mood / theme commentary sentences
  - banned IP or quality terms
  - duplicate captions

Exit code is 1 when problems are found, so it can gate a training run.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

STYLE_RE = re.compile(
    r"\b(art\s?style|anime[- ]style|manga[- ]style|line\s?work|line art|flat colou?rs?|"
    r"cel[- ]shad\w*|toon shad\w*|shading|rendered in|digital (?:art|illustration|painting)|"
    r"colou?r palette)\b",
    re.IGNORECASE,
)
INTERPRETIVE_RE = re.compile(
    r"\b(evokes?|evoking|conveys?|conveying|themes? of|symboli[sz](?:es|m|sm)|a sense of|"
    r"atmosphere|mood of|artist'?s intent|feels? "
    r"(?:dynamic|tense|serene|intimate|melancholic|dreamlike))\b",
    re.IGNORECASE,
)
# Only sentences that the caption cleaner would actually drop count as issues;
# a content sentence such as "an open mouth conveying excitement" is fine.
INTERPRETIVE_LEAD_RE = re.compile(
    r"^(?:the\s+|this\s+|it\s+)?(?:overall\s+)?"
    r"(composition|mood|atmosphere|artwork|picture|image|style)\b",
    re.IGNORECASE,
)
INTERPRETIVE_VERB_LEAD_RE = re.compile(
    r"^(?:the\s+)?(?:scene|lighting|light|colour|color|palette|tonality)\s+"
    r"(?:conveys?|evokes?|suggests?|captures?|expresses?|feels?)\b",
    re.IGNORECASE,
)
TRAILING_INTERPRETIVE_RE = re.compile(
    r"(?:,\s*(?:and\s+)?|\s+and\s+)(?:which\s+)?(creating|evoking|conveying|suggesting|giving|"
    r"adding|resulting in|emphasizing|highlighting|making)\b[^.]*\.",
    re.IGNORECASE,
)


def find_interpretive(text: str):
    """Return the first sentence the cleaner would drop, or ''."""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        trimmed = TRAILING_INTERPRETIVE_RE.sub(".", sentence).strip()
        if INTERPRETIVE_RE.search(trimmed) and (
            INTERPRETIVE_LEAD_RE.match(trimmed) or INTERPRETIVE_VERB_LEAD_RE.match(trimmed)
        ):
            return trimmed
    return ""
BANNED_RE = re.compile(
    r"\b(masterpiece|best quality|signature|watermark|azur lane|arknights|genshin impact|"
    r"honkai|blue archive|hololive|nikke|umamusume)\b",
    re.IGNORECASE,
)


def audit_dir(folder: Path, trigger: str, min_words: int, max_words: int):
    captions = {}
    issues = []
    for txt in sorted(folder.glob("*.txt")):
        stem = txt.with_suffix("")
        if not any(stem.with_suffix(ext).exists() for ext in IMAGE_EXTS):
            issues.append((txt, "orphan: 没有同名图片"))
        text = txt.read_text(encoding="utf-8").strip()
        captions[txt] = text
        if not text:
            issues.append((txt, "empty caption"))
            continue
        if trigger and not text.startswith(trigger):
            issues.append((txt, f"missing trigger prefix {trigger!r}"))
        words = len(text.split())
        if words < min_words:
            issues.append((txt, f"too short: {words} words < {min_words}"))
        elif words > max_words:
            issues.append((txt, f"too long: {words} words > {max_words}"))
        if STYLE_RE.search(text):
            issues.append((txt, "style word: " + STYLE_RE.search(text).group(0)))
        interpretive = find_interpretive(text)
        if interpretive:
            issues.append((txt, "interpretive: " + interpretive[:80]))
        if BANNED_RE.search(text):
            issues.append((txt, "banned term: " + BANNED_RE.search(text).group(0)))

    seen = {}
    for path, text in captions.items():
        if not text:
            continue
        if text in seen:
            issues.append((path, f"duplicate of {seen[text].name}"))
        else:
            seen[text] = path
    return len(captions), issues


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dirs", nargs="+")
    parser.add_argument("--trigger", default="")
    parser.add_argument("--min-words", type=int, default=60)
    parser.add_argument("--max-words", type=int, default=260)
    args = parser.parse_args()

    total_issues = 0
    for raw in args.dirs:
        folder = Path(raw)
        if not folder.is_dir():
            print(f"[audit] skip missing folder: {folder}")
            continue
        count, issues = audit_dir(folder, args.trigger, args.min_words, args.max_words)
        total_issues += len(issues)
        print(f"[audit] {folder}: {count} captions, {len(issues)} issues")
        for path, message in issues[:40]:
            print(f"    {path.name}: {message}")
        if len(issues) > 40:
            print(f"    ... {len(issues) - 40} more")
    return 1 if total_issues else 0


if __name__ == "__main__":
    sys.exit(main())
