import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from task_agent_core.nodes import run_task_direct  # noqa: E402


def parse_int_list(text):
    values = []
    for item in str(text or "").split(","):
        item = item.strip()
        if not item:
            continue
        values.append(int(item))
    return values


def run_once(args, n_gpu_layers, n_batch, unload_after_run):
    start = time.time()
    response = run_task_direct(
        gateway_url="http://127.0.0.1:8765",
        backend_provider="llama_cpp_python_inproc",
        task_type="translate_anime_tags",
        inputs={
            "raw_text": args.prompt,
            "direction": "zh_to_en_tags",
            "target_profile": "generic_tag_model",
        },
        temperature=0.1,
        max_tokens=args.max_tokens,
        auto_load_backend=True,
        unload_after_run=unload_after_run,
        backend_profile=args.profile,
        context_size=args.context_size,
        custom_model_path="",
        custom_mmproj_path="",
        runtime_options={
            "llama_cpp_python_n_gpu_layers": n_gpu_layers,
            "llama_cpp_python_n_batch": n_batch,
        },
    )
    elapsed = time.time() - start
    return {
        "profile": args.profile,
        "context_size": args.context_size,
        "n_gpu_layers": n_gpu_layers,
        "n_batch": n_batch,
        "unload_after_run": unload_after_run,
        "elapsed_sec": round(elapsed, 3),
        "status": response.get("status", "unknown"),
        "raw_preview": str(response.get("raw_text", ""))[:240],
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark Task Agent llama-cpp-python in-process runtime.")
    parser.add_argument("--profile", default="gemma4_e4b_q4")
    parser.add_argument("--context-size", type=int, default=1024)
    parser.add_argument("--max-tokens", type=int, default=120)
    parser.add_argument("--layers", default="0,10")
    parser.add_argument("--batches", default="512")
    parser.add_argument("--prompt", default="white hair girl, blue eyes")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--keep-loaded", action="store_true", help="Run one warm pass and keep model loaded between tests.")
    args = parser.parse_args()

    results = []
    layers = parse_int_list(args.layers)
    batches = parse_int_list(args.batches)
    for n_gpu_layers in layers:
        for n_batch in batches:
            for repeat_index in range(args.repeats):
                result = run_once(args, n_gpu_layers, n_batch, unload_after_run=not args.keep_loaded)
                result["repeat_index"] = repeat_index + 1
                print(json.dumps(result, ensure_ascii=False), flush=True)
                results.append(result)

    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
