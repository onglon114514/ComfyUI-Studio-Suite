import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path


LEGACY_STQ_TYPE = 42
CURRENT_STQ_TYPE = 43
EXPECTED_STQ_TENSORS = 224
KNOWN_LEGACY_HASHES = {
    "93e025c93cc082e73a3f142b757623a8b9cf541c020a8013ca4ee669556860ab",
    "987121bc98dd7107078019f63e72447d67b224efe97da75811e74529e22e3525",
    "53056802fa65c7c7b4563d82dac5d89a8b195cd7e94dd32c571bda4bb5a165eb",
}
SCALAR_SIZES = {
    0: 1,
    1: 1,
    2: 2,
    3: 2,
    4: 4,
    5: 4,
    6: 4,
    7: 1,
    10: 8,
    11: 8,
    12: 8,
}


def _read_exact(handle, size):
    value = handle.read(size)
    if len(value) != size:
        raise ValueError("unexpected end of GGUF file")
    return value


def _read_u32(handle):
    return struct.unpack("<I", _read_exact(handle, 4))[0]


def _read_u64(handle):
    return struct.unpack("<Q", _read_exact(handle, 8))[0]


def _skip_string(handle):
    handle.seek(_read_u64(handle), 1)


def _skip_value(handle, value_type):
    if value_type in SCALAR_SIZES:
        handle.seek(SCALAR_SIZES[value_type], 1)
        return
    if value_type == 8:
        _skip_string(handle)
        return
    if value_type == 9:
        element_type = _read_u32(handle)
        count = _read_u64(handle)
        for _ in range(count):
            _skip_value(handle, element_type)
        return
    raise ValueError(f"unsupported GGUF metadata value type: {value_type}")


def inspect_tensor_types(path):
    positions = []
    counts = {}
    with Path(path).open("rb") as handle:
        if _read_exact(handle, 4) != b"GGUF":
            raise ValueError("not a little-endian GGUF file")
        version = _read_u32(handle)
        if version not in {2, 3}:
            raise ValueError(f"unsupported GGUF version: {version}")
        tensor_count = _read_u64(handle)
        metadata_count = _read_u64(handle)
        for _ in range(metadata_count):
            _skip_string(handle)
            _skip_value(handle, _read_u32(handle))
        for _ in range(tensor_count):
            _skip_string(handle)
            dimension_count = _read_u32(handle)
            handle.seek(8 * dimension_count, 1)
            type_position = handle.tell()
            tensor_type = _read_u32(handle)
            handle.seek(8, 1)
            counts[tensor_type] = counts.get(tensor_type, 0) + 1
            if tensor_type == LEGACY_STQ_TYPE:
                positions.append(type_position)
    return {
        "version": version,
        "tensor_count": tensor_count,
        "type_counts": counts,
        "legacy_type_positions": positions,
    }


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def patch_file(source, output, allow_unknown=False, overwrite=False):
    source = Path(source).resolve()
    output = Path(output).resolve()
    inspection = inspect_tensor_types(source)
    legacy_count = len(inspection["legacy_type_positions"])
    current_count = inspection["type_counts"].get(CURRENT_STQ_TYPE, 0)
    if legacy_count == 0:
        if current_count:
            return {"status": "already_compatible", "source": str(source), "type43_tensors": current_count}
        raise ValueError("no legacy or current STQ tensor types were found")
    source_hash = sha256_file(source)
    if source_hash not in KNOWN_LEGACY_HASHES and not allow_unknown:
        raise ValueError(
            "source hash is not a known official legacy Hy-MT GGUF; "
            "use --allow-unknown only after manually verifying the file"
        )
    if legacy_count != EXPECTED_STQ_TENSORS and not allow_unknown:
        raise ValueError(f"expected {EXPECTED_STQ_TENSORS} legacy STQ tensors, found {legacy_count}")
    if output == source:
        raise ValueError("in-place patching is not supported")
    if output.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    shutil.copyfile(source, temporary)
    try:
        with temporary.open("r+b") as handle:
            for position in inspection["legacy_type_positions"]:
                handle.seek(position)
                if _read_u32(handle) != LEGACY_STQ_TYPE:
                    raise ValueError(f"unexpected tensor type at byte {position}")
                handle.seek(position)
                handle.write(struct.pack("<I", CURRENT_STQ_TYPE))
        patched = inspect_tensor_types(temporary)
        if patched["type_counts"].get(LEGACY_STQ_TYPE, 0):
            raise ValueError("legacy STQ tensor types remain after patch")
        if patched["type_counts"].get(CURRENT_STQ_TYPE, 0) != legacy_count + current_count:
            raise ValueError("patched STQ tensor count does not match")
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "status": "patched",
        "source": str(source),
        "output": str(output),
        "source_sha256": source_hash,
        "output_sha256": sha256_file(output),
        "patched_tensor_types": legacy_count,
        "size": output.stat().st_size,
    }


def main():
    parser = argparse.ArgumentParser(description="Patch known legacy Hy-MT STQ tensor type IDs from 42 to 43.")
    parser.add_argument("source")
    parser.add_argument("--output")
    parser.add_argument("--allow-unknown", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    source = Path(args.source)
    output = Path(args.output) if args.output else source.with_name(f"{source.stem}-type43{source.suffix}")
    try:
        result = patch_file(source, output, allow_unknown=args.allow_unknown, overwrite=args.overwrite)
    except Exception as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
