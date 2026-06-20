import hashlib
import json
from pathlib import Path

import folder_paths


def _read_safetensors_metadata(file_path):
    with open(file_path, "rb") as handle:
        header_size = int.from_bytes(handle.read(8), "little", signed=False)
        if header_size <= 0:
            return {}
        header = handle.read(header_size)
        if not header:
            return {}
        payload = json.loads(header)
        metadata = payload.get("__metadata__") or {}
        normalized = {}
        for key, value in metadata.items():
            if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
                try:
                    normalized[key] = json.loads(value)
                    continue
                except Exception:
                    pass
            normalized[key] = value
        return normalized


def _compute_sha256(file_path):
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _find_model_path(model_type, model_name):
    model_name = str(model_name or "").strip()
    if not model_name:
        return None

    if model_type in {"loras", "embeddings"}:
        lowered = model_name.lower()
        for filename in folder_paths.get_filename_list(model_type):
            full_path = folder_paths.get_full_path(model_type, filename)
            stem = Path(filename).stem.lower()
            if filename.lower() == lowered or stem == lowered:
                return Path(full_path)
        return None

    full_path = folder_paths.get_full_path(model_type, model_name)
    return Path(full_path) if full_path else None


def _extract_trained_words(metadata):
    trained_words = {}
    tag_frequency = metadata.get("ss_tag_frequency")
    if isinstance(tag_frequency, dict):
        for bucket in tag_frequency.values():
            if not isinstance(bucket, dict):
                continue
            for tag, count in bucket.items():
                trained_words[tag] = trained_words.get(tag, 0) + int(count)
    result = [{"word": tag, "count": count} for tag, count in trained_words.items()]
    result.sort(key=lambda item: (-item["count"], item["word"]))
    return result


def _extract_resolution(metadata):
    resolution = metadata.get("ss_resolution")
    if isinstance(resolution, str) and "," in resolution:
        width, height = resolution.replace("(", "").replace(")", "").split(",", 1)
        return f"{width.strip()}x{height.strip()}"
    modelspec = metadata.get("modelspec.resolution")
    if modelspec:
        return str(modelspec)
    return ""


def build_model_payload(model_type, model_name, notes_dir):
    model_path = _find_model_path(model_type, model_name)
    if model_path is None or not model_path.exists():
        return None

    metadata = {}
    if model_path.suffix.lower() == ".safetensors":
        try:
            metadata = _read_safetensors_metadata(model_path)
        except Exception:
            metadata = {}

    sha256 = _compute_sha256(model_path)
    note_path = Path(notes_dir) / model_type / f"{sha256}.txt"
    notes = note_path.read_text(encoding="utf-8") if note_path.exists() else ""

    return {
        "type": model_type,
        "name": Path(model_name).stem,
        "file": model_path.name,
        "path": str(model_path),
        "sha256": sha256,
        "notes": notes,
        "metadata": metadata,
        "trained_words": _extract_trained_words(metadata),
        "base_model": str(metadata.get("ss_sd_model_name") or metadata.get("modelspec.architecture") or ""),
        "resolution": _extract_resolution(metadata),
        "usage_hint": str(metadata.get("modelspec.usage_hint") or ""),
    }


def save_model_notes(model_type, model_name, notes_text, notes_dir):
    payload = build_model_payload(model_type, model_name, notes_dir)
    if payload is None:
        return None

    note_path = Path(notes_dir) / model_type / f"{payload['sha256']}.txt"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(str(notes_text or ""), encoding="utf-8")
    payload["notes"] = str(notes_text or "")
    return payload
