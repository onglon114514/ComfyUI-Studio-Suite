from __future__ import annotations

import atexit
import ctypes
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

if os.name == "nt":
    from ctypes import wintypes


DEFAULT_MODEL_NAME = "Hy-MT1.5-1.8B-1.25bit.gguf"
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


if os.name == "nt":
    class _JobObjectBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]


    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]


    class _JobObjectExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobObjectBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


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


def build_translation_prompt(text: str, direction: str) -> str:
    target = "英语" if str(direction).startswith("zh_to_en") else "中文"
    format_note = ""
    if str(direction).endswith("_tags"):
        format_note = "保持原有逗号分隔、括号、反斜杠和标签顺序，不要把标签改写成解释性段落。"
    return (
        f"将以下文本翻译为{target}，注意只需要输出翻译后的结果，不要额外解释。"
        f"{format_note}\n\n{str(text or '').strip()}"
    )


def clean_translation_output(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text


class HyMtRuntime:
    """Managed CPU-only llama-server for the experimental HY-MT STQ model."""

    def __init__(self, package_dir: Path):
        self.package_dir = Path(package_dir).resolve()
        self.process: subprocess.Popen | None = None
        self.log_handle = None
        self.lock = threading.RLock()
        self.idle_timer: threading.Timer | None = None
        self.stop_requested = threading.Event()
        self.started_at = 0.0
        self.job_handle = None
        self.job_error = ""
        atexit.register(self.stop)

    def _attach_windows_lifetime_job(self, process: subprocess.Popen) -> None:
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = _JobObjectExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(job)
            raise OSError(error, "SetInformationJobObject failed")
        if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(job)
            raise OSError(error, "AssignProcessToJobObject failed")
        self.job_handle = job

    def _close_windows_lifetime_job(self) -> None:
        if os.name == "nt" and self.job_handle:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(wintypes.HANDLE(self.job_handle))
        self.job_handle = None

    def _config(self) -> dict:
        local_path = self.package_dir / "config" / "hy_mt_runtime.local.json"
        example_path = self.package_dir / "config" / "hy_mt_runtime.example.json"
        config = _read_json(local_path) if local_path.exists() else _read_json(example_path)
        config["_source"] = str(local_path if local_path.exists() else example_path)
        return config

    def _paths(self, config: dict) -> tuple[Path | None, Path | None]:
        executable = _resolve_path(
            self.package_dir,
            os.environ.get("STUDIO_SUITE_HY_MT_SERVER") or config.get("server_executable", ""),
            [
                self.package_dir / "runtime" / "hy_mt" / "llama-server.exe",
                self.package_dir / "runtime" / "hy_mt" / "llama-server",
            ],
        )
        model = _resolve_path(
            self.package_dir,
            os.environ.get("STUDIO_SUITE_HY_MT_MODEL") or config.get("model_path", ""),
            [self.package_dir / "models" / "hy_mt" / DEFAULT_MODEL_NAME],
        )
        return executable, model

    def _alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def allow_start(self) -> None:
        self.stop_requested.clear()

    def status(self) -> dict:
        config = self._config()
        executable, model = self._paths(config)
        enabled = bool(config.get("enabled", True))
        available = bool(enabled and executable and executable.is_file() and model and model.is_file())
        reason = ""
        if not enabled:
            reason = "disabled"
        elif not executable or not executable.is_file():
            reason = "server_executable_missing"
        elif not model or not model.is_file():
            reason = "model_missing"
        return {
            "available": available,
            "alive": self._alive(),
            "pid": self.process.pid if self._alive() else None,
            "reason": reason,
            "server_executable": str(executable or ""),
            "model_path": str(model or ""),
            "config_source": str(config.get("_source", "")),
            "port": int(config.get("port", 8768) or 8768),
            "experimental": bool(config.get("experimental_stq", True)),
            "parent_lifetime_guard": (
                "windows_job_object"
                if self.job_handle
                else (
                    "degraded"
                    if self.job_error
                    else ("atexit_only" if os.name != "nt" and self._alive() else "not_started")
                )
            ),
            "parent_lifetime_guard_error": self.job_error,
            "uptime_seconds": round(max(0.0, time.monotonic() - self.started_at), 2) if self._alive() else 0.0,
        }

    @staticmethod
    def _health(port: int, timeout: float = 1.0) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
                return 200 <= int(response.status) < 300
        except Exception:
            return False

    def ensure_started(self) -> dict:
        with self.lock:
            if self.stop_requested.is_set():
                raise RuntimeError("HY-MT start is not authorized after a cleanup request")
            config = self._config()
            executable, model = self._paths(config)
            state = self.status()
            if not state["available"]:
                raise RuntimeError(f"HY-MT runtime unavailable: {state['reason']}")
            port = int(config.get("port", 8768) or 8768)
            if self._alive() and self._health(port):
                return self.status()
            if self._alive():
                self.stop()
            if self._health(port):
                raise RuntimeError(f"HY-MT port {port} is already used by another process")

            threads = int(config.get("threads", 0) or 0)
            if threads <= 0:
                threads = max(2, min(12, (os.cpu_count() or 4) - 1))
            context_size = max(256, min(4096, int(config.get("context_size", 1024) or 1024)))
            command = [
                str(executable),
                "--model",
                str(model),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--ctx-size",
                str(context_size),
                "--threads",
                str(threads),
                "--n-gpu-layers",
                "0",
                "--no-webui",
            ]
            log_dir = self.package_dir / "runtime" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            self.log_handle = (log_dir / "hy_mt_server.log").open("a", encoding="utf-8")
            creationflags = 0
            if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
                creationflags = subprocess.CREATE_NO_WINDOW
            self.process = subprocess.Popen(
                command,
                cwd=str(executable.parent),
                stdin=subprocess.DEVNULL,
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            self.job_error = ""
            try:
                self._attach_windows_lifetime_job(self.process)
            except Exception as error:
                self.job_error = str(error)
            self.started_at = time.monotonic()
            deadline = time.monotonic() + max(5, int(config.get("startup_timeout_sec", 60) or 60))
            while time.monotonic() < deadline:
                if self.stop_requested.is_set():
                    self.stop()
                    raise RuntimeError("HY-MT startup interrupted by cleanup request")
                if self.process.poll() is not None:
                    returncode = self.process.returncode
                    self.stop()
                    raise RuntimeError(f"HY-MT llama-server exited with code {returncode}")
                if self._health(port):
                    return self.status()
                time.sleep(0.2)
            self.stop()
            raise RuntimeError("HY-MT llama-server startup timed out")

    def _request_completion(self, prompt: str, max_tokens: int, temperature: float) -> str:
        config = self._config()
        port = int(config.get("port", 8768) or 8768)
        payload = json.dumps(
            {
                "prompt": prompt,
                "n_predict": max(8, min(2048, int(max_tokens))),
                "temperature": max(0.0, min(1.0, float(temperature))),
                "top_p": 0.9,
                "stop": ["</s>", "<|eot_id|>", "<|im_end|>"],
                "cache_prompt": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/completion",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = max(5, int(config.get("request_timeout_sec", 90) or 90))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"HY-MT HTTP {error.code}: {details}") from error
        translated = clean_translation_output(result.get("content", "") if isinstance(result, dict) else "")
        if not translated:
            raise RuntimeError("HY-MT returned an empty translation")
        return translated

    def translate(self, text: str, direction: str, max_tokens: int = 256, temperature: float = 0.05) -> dict:
        started = time.monotonic()
        self.ensure_started()
        translated = self._request_completion(
            build_translation_prompt(text, direction),
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return {
            "translated_text": translated,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "runtime": self.status(),
        }

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

    def stop(self) -> dict:
        self.stop_requested.set()
        with self.lock:
            if self.idle_timer is not None:
                self.idle_timer.cancel()
                self.idle_timer = None
            process = self.process
            self.process = None
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
                    process.wait(timeout=3)
            if self.log_handle is not None:
                try:
                    self.log_handle.close()
                except Exception:
                    pass
                self.log_handle = None
            self._close_windows_lifetime_job()
            self.started_at = 0.0
            return {"status": "stopped" if process is not None else "idle"}
