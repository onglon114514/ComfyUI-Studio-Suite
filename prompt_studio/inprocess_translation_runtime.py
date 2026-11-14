from __future__ import annotations

import atexit
import gc
import importlib.util
import json
import os
import threading
import time
from pathlib import Path

from .translation_core import TRANSLATION_CACHE_LIMIT, TagTranslator, TranslationCache

DEFAULT_MODEL_NAME = "HY-MT1.5-1.8B-Q4_K_M.gguf"
DLL_HANDLES: list = []
_GATEWAY_MODULE = None


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_path(package_dir: Path, value: str, fallbacks: list[Path]) -> Path | None:
    text = str(value or "").strip()
    candidates = []
    if text:
        configured = Path(text).expanduser()
        candidates.append(configured if configured.is_absolute() else package_dir / configured)
    candidates.extend(fallbacks)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve() if candidates else None


class InProcessTranslationRuntime:
    """CPU-only llama.cpp translation backend living inside ComfyUI's process.

    Uses a standard-quant HY-MT GGUF (Q4_K_M / Q6_K / Q8_0) so the public
    llama-cpp-python wheel can load it; STQ1_0 is not supported upstream yet.
    """

    def __init__(self, package_dir: Path):
        self.package_dir = Path(package_dir).resolve()
        self.model = None
        self.model_key = ""
        self.lock = threading.RLock()
        self.idle_timer: threading.Timer | None = None
        self.started_at = 0.0
        self.translation_cache = TranslationCache(TRANSLATION_CACHE_LIMIT)
        self.last_error = ""
        self.llama_cpp_info: dict = {}
        atexit.register(self.stop)

    def _config(self) -> dict:
        local_path = self.package_dir / "config" / "inprocess_translation.local.json"
        example_path = self.package_dir / "config" / "inprocess_translation.example.json"
        config = _read_json(local_path) if local_path.exists() else _read_json(example_path)
        config["_source"] = str(local_path if local_path.exists() else example_path)
        return config

    def model_path(self, config: dict | None = None) -> Path | None:
        config = config or self._config()
        return _resolve_path(
            self.package_dir,
            os.environ.get("STUDIO_SUITE_HYMT_Q4_MODEL") or config.get("model_path", ""),
            [
                self.package_dir / "models" / "hy_mt" / DEFAULT_MODEL_NAME,
                self.package_dir / "models" / "hy_mt" / "Hy-MT1.5-1.8B-Q6_K.gguf",
                self.package_dir / "models" / "hy_mt" / "Hy-MT1.5-1.8B-Q8_0.gguf",
            ],
        )

    def status(self) -> dict:
        config = self._config()
        model = self.model_path(config)
        enabled = bool(config.get("enabled", True))
        available = bool(enabled and model and model.is_file())
        reason = ""
        if not enabled:
            reason = "disabled"
        elif not model or not model.is_file():
            reason = "model_missing"
        return {
            "available": available,
            "alive": self.model is not None,
            "loaded": self.model is not None,
            "reason": reason,
            "model_path": str(model or ""),
            "config_source": str(config.get("_source", "")),
            "threads": int(config.get("threads", 0) or 0),
            "context_size": int(config.get("context_size", 1024) or 1024),
            "n_gpu_layers": int(config.get("n_gpu_layers", 0) or 0),
            "uptime_seconds": round(max(0.0, time.monotonic() - self.started_at), 2) if self.model is not None else 0.0,
            "llama_cpp": self.llama_cpp_info,
            "last_error": self.last_error,
        }

    def _load_llama_class(self, config: dict):
        global _GATEWAY_MODULE
        if _GATEWAY_MODULE is None:
            gateway_path = self.package_dir / "task_agent_gateway.py"
            if gateway_path.is_file():
                spec = importlib.util.spec_from_file_location(
                    "prompt_studio_inprocess_task_agent_gateway", str(gateway_path)
                )
                if spec is not None and spec.loader is not None:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    _GATEWAY_MODULE = module
        if _GATEWAY_MODULE is not None:
            prefer_private = bool(config.get("llama_cpp_prefer_private", False))
            return _GATEWAY_MODULE.load_llama_cpp_for_task_agent(prefer_private=prefer_private)
        public_spec = importlib.util.find_spec("llama_cpp")
        if public_spec is None or not public_spec.origin:
            raise RuntimeError("llama-cpp-python is not installed")
        lib_dir = Path(public_spec.origin).resolve().parent / "lib"
        if lib_dir.is_dir() and hasattr(os, "add_dll_directory"):
            DLL_HANDLES.append(os.add_dll_directory(str(lib_dir)))
        from llama_cpp import Llama

        return Llama, {}

    def ensure_started(self) -> dict:
        config = self._config()
        model = self.model_path(config)
        state = self.status()
        if not state["available"] or model is None:
            raise RuntimeError(f"in-process translation runtime unavailable: {state['reason']}")
        with self.lock:
            model_key = str(model)
            if self.model is not None and self.model_key == model_key:
                return self.status()
            if self.model is not None:
                self._unload_locked()
            Llama, info = self._load_llama_class(config)
            threads = int(config.get("threads", 0) or 0)
            if threads <= 0:
                threads = max(2, min(12, (os.cpu_count() or 4) - 1))
            context_size = max(256, min(8192, int(config.get("context_size", 1024) or 1024)))
            self.model = Llama(
                model_path=model_key,
                n_ctx=context_size,
                n_threads=threads,
                n_gpu_layers=max(0, int(config.get("n_gpu_layers", 0) or 0)),
                n_batch=max(32, int(config.get("n_batch", 256) or 256)),
                verbose=False,
            )
            self.model_key = model_key
            self.started_at = time.monotonic()
            self.llama_cpp_info = info if isinstance(info, dict) else {}
            self.last_error = ""
            return self.status()

    def _complete(self, prompt: str, max_tokens: int, temperature: float) -> tuple[str, dict]:
        with self.lock:
            if self.model is None:
                raise RuntimeError("in-process translation model is not loaded")
            response = self.model.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max(8, min(2048, int(max_tokens))),
                temperature=max(0.0, min(1.0, float(temperature))),
                top_p=0.9,
            )
        choices = response.get("choices", []) if isinstance(response, dict) else []
        message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        if not str(content or "").strip():
            raise RuntimeError("in-process translation returned an empty result")
        timings = response.get("timings", {}) if isinstance(response, dict) else {}
        return str(content), timings if isinstance(timings, dict) else {}

    def translate(self, text: str, direction: str, max_tokens: int = 256, temperature: float = 0.05) -> dict:
        started = False

        def complete(prompt, tokens, temp):
            nonlocal started
            if not started:
                self.ensure_started()
                started = True
            return self._complete(prompt, tokens, temp)

        try:
            translator = TagTranslator(complete, self.translation_cache)
            result = translator.translate(
                text,
                direction,
                max_tokens=max_tokens,
                temperature=temperature,
                cache_namespace=str(self.status().get("model_path", "") or ""),
            )
        except Exception as error:
            self.last_error = str(error)
            raise
        result["runtime"] = self.status()
        return result

    def schedule_idle_shutdown(self, idle_seconds: int | None = None):
        with self.lock:
            config = self._config()
            seconds = int(idle_seconds if idle_seconds is not None else config.get("idle_timeout_sec", 600) or 600)
            if self.idle_timer is not None:
                self.idle_timer.cancel()
                self.idle_timer = None
            if seconds <= 0:
                return
            self.idle_timer = threading.Timer(float(seconds), self.stop)
            self.idle_timer.daemon = True
            self.idle_timer.start()

    def _unload_locked(self) -> bool:
        had_model = self.model is not None
        model = self.model
        self.model = None
        self.model_key = ""
        self.started_at = 0.0
        if model is not None:
            try:
                model.close()
            except Exception:
                pass
            gc.collect()
        return had_model

    def stop(self) -> dict:
        with self.lock:
            if self.idle_timer is not None:
                self.idle_timer.cancel()
                self.idle_timer = None
            had_model = self._unload_locked()
        return {"status": "stopped" if had_model else "idle"}
