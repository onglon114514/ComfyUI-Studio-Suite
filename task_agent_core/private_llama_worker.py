import json
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import task_agent_gateway as gateway  # noqa: E402


def build_worker_config(payload):
    config_path = gateway.default_gateway_config_path()
    config = gateway.load_json_file(config_path)
    config = dict(config)
    backend = dict(config.get("backend", {}) or {})
    backend["mode"] = "inprocess"
    backend["provider"] = "llama_cpp_python_inproc"
    backend["llama_cpp_python_prefer_private"] = True
    backend["managed_process_window_mode"] = "hidden"
    config["backend"] = backend
    return config


def run_payload(backend, payload):
    return backend.run_task(
        task_type=payload.get("task_type", ""),
        inputs=payload.get("inputs", {}) or {},
        temperature=float(payload.get("temperature", 0.4)),
        max_tokens=int(payload.get("max_tokens", 900)),
        auto_load_backend=bool(payload.get("auto_load_backend", True)),
        unload_after_run=bool(payload.get("unload_after_run", True)),
        backend_profile=payload.get("backend_profile"),
        context_size=payload.get("context_size"),
        custom_model_path=payload.get("custom_model_path"),
        custom_mmproj_path=payload.get("custom_mmproj_path"),
        runtime_options=payload.get("runtime_options", {}) or {},
    )


def print_response(payload, response):
    request_id = payload.get("request_id")
    if request_id is not None:
        response["request_id"] = request_id
    print(json.dumps(response, ensure_ascii=False), flush=True)


def main_once():
    payload = json.loads(sys.stdin.read() or "{}")
    backend = gateway.ManagedBackend(build_worker_config(payload))
    try:
        result = run_payload(backend, payload)
        print_response(payload, {"ok": True, "result": result})
        return 0
    except Exception as error:
        print_response(
            payload,
            {
                "ok": False,
                "error": str(error),
                "error_type": type(error).__name__,
            },
        )
        return 1
    finally:
        try:
            backend.unload()
        except Exception:
            pass


def main_stdio_server():
    backend = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception as error:
            print_response({}, {"ok": False, "error": str(error), "error_type": type(error).__name__})
            continue

        command = str(payload.get("command", "") or "").strip()
        if command == "shutdown":
            if backend is not None:
                try:
                    backend.unload()
                except Exception:
                    pass
            print_response(payload, {"ok": True, "result": {"status": "shutdown"}})
            return 0
        if command == "unload":
            if backend is not None:
                try:
                    result = backend.unload()
                except Exception as error:
                    print_response(payload, {"ok": False, "error": str(error), "error_type": type(error).__name__})
                    continue
            else:
                result = {"status": "idle"}
            print_response(payload, {"ok": True, "result": result})
            continue
        if command == "preload":
            if backend is None:
                backend = gateway.ManagedBackend(build_worker_config(payload))
            try:
                result = backend.ensure_loaded(
                    backend_profile=payload.get("backend_profile"),
                    context_size=payload.get("context_size"),
                    custom_model_path=payload.get("custom_model_path"),
                    custom_mmproj_path=None,
                    runtime_options=payload.get("runtime_options", {}) or {},
                )
                print_response(payload, {"ok": True, "result": result})
            except Exception as error:
                print_response(payload, {"ok": False, "error": str(error), "error_type": type(error).__name__})
            continue

        if backend is None:
            backend = gateway.ManagedBackend(build_worker_config(payload))
        try:
            result = run_payload(backend, payload)
            print_response(payload, {"ok": True, "result": result})
        except Exception as error:
            print_response(
                payload,
                {
                    "ok": False,
                    "error": str(error),
                    "error_type": type(error).__name__,
                },
            )

    if backend is not None:
        try:
            backend.unload()
        except Exception:
            pass
    return 0


def main():
    if "--stdio-server" in sys.argv:
        return main_stdio_server()
    return main_once()


if __name__ == "__main__":
    raise SystemExit(main())
