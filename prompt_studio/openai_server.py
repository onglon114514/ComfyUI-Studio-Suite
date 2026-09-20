"""Local OpenAI-compatible HTTP surface for ComfyUI Studio Suite.

External agents (OpenClaw / Codex / Claude Code / ...) can use the node's local
LLM as an OpenAI-compatible model source — including vision profiles that accept
base64 ``image_url`` data URLs — without configuring any API key on either side.

Safety model (mirrors the rest of the package):
- Disabled by default. Enable explicitly via
  ``config/local_openai_server.local.json`` (see the example file) or the
  ``STUDIO_SUITE_LOCAL_OPENAI_ENABLED`` environment override.
- Loopback only: ComfyUI may bind ``0.0.0.0``, so every request is checked
  against the peer address instead of relying on the listen address.
- Single-flight: one LLM request at a time (429 while busy) plus a hard
  timeout, so a long completion can never pile up behind — or block — the
  ComfyUI image queue. The queue-guard hook in ``prompt_studio/api.py``
  additionally unloads the shared backend before a workflow is accepted.
- Request body / image size limits; errors use the standard OpenAI error
  envelope ``{"error": {"message", "type", "param", "code"}}``.

The module is import-safe outside ComfyUI (no ``server``/``folder_paths``
imports) so the unit tests can load it standalone and inject a fake backend.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import json
import os
import queue as thread_queue
import threading
import time
import uuid
from pathlib import Path

from aiohttp import web


MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_PACKAGE_DIR = MODULE_DIR.parent

NAMESPACED_PREFIX = "/studio-suite/llm"
MODELS_PATH = "/v1/models"
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"

ENABLE_ENV_VAR = "STUDIO_SUITE_LOCAL_OPENAI_ENABLED"

DEFAULT_CONFIG = {
    "enabled": False,
    "max_request_bytes": 32 * 1024 * 1024,
    "max_image_bytes": 10 * 1024 * 1024,
    "max_images_per_request": 4,
    "max_messages": 64,
    "request_timeout_sec": 600,
    "allow_remote_image_url": False,
    "default_backend_profile": "",
    "queue_guard": True,
}

AUTO_MODEL_NAMES = {"", "auto", "default"}
OPTION_PASSTHROUGH = ("top_p", "stop", "seed", "frequency_penalty", "presence_penalty")


class RequestValidationError(Exception):
    def __init__(self, message, *, status=400, err_type="invalid_request_error", code=None, param=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.err_type = err_type
        self.code = code
        self.param = param


def _read_json_best_effort(path: Path):
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except Exception:
            continue
    return None


def load_server_config(package_dir: Path | None = None) -> dict:
    """Merge defaults <- example <- local overrides."""
    package_dir = Path(package_dir) if package_dir else DEFAULT_PACKAGE_DIR
    config_dir = package_dir / "config"
    config = dict(DEFAULT_CONFIG)
    for name in ("local_openai_server.example.json", "local_openai_server.local.json"):
        path = config_dir / name
        if not path.exists():
            continue
        loaded = _read_json_best_effort(path)
        if isinstance(loaded, dict):
            for key in DEFAULT_CONFIG:
                if key in loaded:
                    config[key] = loaded[key]
    return config


def resolve_enabled(config: dict) -> bool:
    override = os.environ.get(ENABLE_ENV_VAR, "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    return bool(config.get("enabled", False))


def load_backend_profiles(package_dir: Path | None = None) -> dict:
    """Merge backend_profiles.example.json <- backend_profiles.json (same rule
    as prompt_studio/api.py so both surfaces agree on the catalog)."""
    package_dir = Path(package_dir) if package_dir else DEFAULT_PACKAGE_DIR
    profiles: dict = {}
    for name in ("backend_profiles.example.json", "backend_profiles.json"):
        path = package_dir / "config" / name
        if not path.exists():
            continue
        loaded = _read_json_best_effort(path)
        if isinstance(loaded, dict):
            profiles.update(loaded)
    return profiles


def is_vision_profile(profile_key: str, profile: dict) -> bool:
    """Vision criterion: the profile key conventionally contains ``vision``;
    explicit ``supports_vision``/``mmproj_path`` markers also count."""
    if not isinstance(profile, dict):
        return False
    if "vision" in str(profile_key or "").lower():
        return True
    return bool(profile.get("supports_vision")) or bool(str(profile.get("mmproj_path") or "").strip())


def build_models_payload(profiles: dict) -> dict:
    created = int(time.time())
    data = []
    for key in sorted(profiles):
        profile = profiles[key] if isinstance(profiles[key], dict) else {}
        data.append({
            "id": key,
            "object": "model",
            "created": created,
            "owned_by": "comfyui-studio-suite",
            "permission": [],
            "root": key,
            "parent": None,
            # Non-standard discovery fields; harmless for OpenAI clients.
            "supports_vision": is_vision_profile(key, profile),
            "display_name": str(profile.get("display_name") or key),
            "default_context_size": int(profile.get("default_context_size", 0) or 0),
        })
    return {"object": "list", "data": data}


def remote_is_loopback(remote) -> bool:
    if not remote:
        return False
    try:
        addr = ipaddress.ip_address(str(remote))
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return bool(addr.is_loopback)


def openai_error_payload(message: str, err_type: str, code=None, param=None) -> dict:
    return {"error": {"message": str(message), "type": err_type, "param": param, "code": code}}


def openai_error_response(message: str, status: int, err_type: str, code=None, param=None) -> web.Response:
    return web.json_response(openai_error_payload(message, err_type, code, param), status=status)


def _estimate_tokens(text: str) -> int:
    text = str(text or "")
    if not text:
        return 0
    # Rough heuristic only; the node does not run a tokenizer for accounting.
    return max(1, len(text) // 4)


def _messages_text(messages) -> str:
    parts = []
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
    return "\n".join(parts)


def validate_image_url(url: str, config: dict) -> str:
    """Return the URL unchanged when acceptable; raise otherwise.

    Only base64 data URLs are accepted by default. Remote http(s) URLs are
    rejected unless ``allow_remote_image_url`` is enabled, so the node never
    fetches arbitrary network resources on behalf of a client.
    """
    text = str(url or "").strip()
    if not text:
        raise RequestValidationError("image_url is empty", code="invalid_image_url")
    if text.startswith("data:"):
        header, separator, encoded = text[5:].partition(",")
        if not separator or ";base64" not in header:
            raise RequestValidationError(
                "image data URL must be base64 encoded (data:image/...;base64,...)",
                code="invalid_image_url",
            )
        mime_type = header.split(";", 1)[0].strip().lower()
        if not mime_type.startswith("image/"):
            raise RequestValidationError(
                f"image data URL must use an image/* media type, got {mime_type or '<missing>'}",
                code="invalid_image_url",
            )
        max_bytes = int(config.get("max_image_bytes", DEFAULT_CONFIG["max_image_bytes"]))
        # Reject by encoded length before decoding anything large.
        if (len(encoded) * 3) // 4 > max_bytes:
            raise RequestValidationError(
                f"image exceeds the {max_bytes}-byte limit",
                status=413,
                err_type="invalid_request_error",
                code="image_too_large",
            )
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise RequestValidationError(
                f"image data URL is not valid base64: {error}",
                code="invalid_image_url",
            ) from error
        if len(decoded) > max_bytes:
            raise RequestValidationError(
                f"image exceeds the {max_bytes}-byte limit",
                status=413,
                err_type="invalid_request_error",
                code="image_too_large",
            )
        return text
    if text.startswith(("http://", "https://")):
        if bool(config.get("allow_remote_image_url", False)):
            return text
        raise RequestValidationError(
            "remote image_url is not allowed; send a base64 data URL instead",
            code="remote_image_url_not_allowed",
        )
    raise RequestValidationError(
        "unsupported image_url scheme; only base64 data URLs are accepted",
        code="invalid_image_url",
    )


def resolve_profile_key(model: str, profiles: dict, config: dict, has_images: bool) -> str:
    requested = str(model or "").strip()
    if requested and requested.lower() not in AUTO_MODEL_NAMES:
        key = requested
        if key not in profiles:
            key = next(
                (name for name, profile in profiles.items()
                 if isinstance(profile, dict) and str(profile.get("model_name") or "") == requested),
                None,
            )
        if key is None or key not in profiles:
            raise RequestValidationError(
                f"unknown model: {requested}. Call GET /v1/models for the available backend profiles.",
                status=404,
                err_type="invalid_request_error",
                code="model_not_found",
                param="model",
            )
        if has_images and not is_vision_profile(key, profiles[key]):
            raise RequestValidationError(
                f"model {key} does not support image input; choose a vision backend profile",
                err_type="invalid_request_error",
                code="model_not_vision_capable",
                param="model",
            )
        return key

    preferred = str(config.get("default_backend_profile") or "").strip()
    if preferred and preferred in profiles:
        if not has_images or is_vision_profile(preferred, profiles[preferred]):
            return preferred

    candidates = [
        key for key, profile in profiles.items()
        if not has_images or is_vision_profile(key, profile)
    ]
    if not candidates:
        raise RequestValidationError(
            "no vision-capable backend profile is configured"
            if has_images else
            "no backend profile is configured",
            err_type="invalid_request_error",
            code="no_suitable_backend_profile",
        )
    if has_images:
        # The catalog convention is that vision profile keys contain "vision".
        candidates.sort(key=lambda name: (0 if "vision" in name.lower() else 1, name.lower()))
    else:
        candidates.sort(key=str.lower)
    return candidates[0]


def normalize_chat_payload(raw, profiles: dict, config: dict) -> dict:
    if not isinstance(raw, dict):
        raise RequestValidationError("request body must be a JSON object", code="invalid_json")
    messages = raw.get("messages")
    if not isinstance(messages, list) or not messages:
        raise RequestValidationError("messages must be a non-empty array", code="invalid_messages", param="messages")
    if len(messages) > int(config.get("max_messages", DEFAULT_CONFIG["max_messages"])):
        raise RequestValidationError(
            f"too many messages (limit {config.get('max_messages')})",
            code="too_many_messages",
            param="messages",
        )

    normalized_messages = []
    image_count = 0
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise RequestValidationError(f"messages[{index}] must be an object", code="invalid_messages")
        role = str(message.get("role", "user") or "user").strip().lower()
        if role not in {"system", "user", "assistant", "tool"}:
            raise RequestValidationError(
                f"messages[{index}].role is not supported: {role}",
                code="invalid_role",
            )
        content = message.get("content", "")
        if isinstance(content, str):
            normalized_messages.append({"role": role, "content": content})
            continue
        if isinstance(content, list):
            parts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type == "text":
                    parts.append({"type": "text", "text": str(part.get("text", ""))})
                elif part_type == "image_url":
                    image_count += 1
                    image_field = part.get("image_url")
                    url = image_field.get("url") if isinstance(image_field, dict) else image_field
                    validated_url = validate_image_url(str(url or ""), config)
                    normalized_part = {"type": "image_url", "image_url": {"url": validated_url}}
                    if isinstance(image_field, dict) and image_field.get("detail"):
                        normalized_part["image_url"]["detail"] = str(image_field.get("detail"))
                    parts.append(normalized_part)
                else:
                    raise RequestValidationError(
                        f"unsupported content part type: {part_type}",
                        code="unsupported_content_part",
                    )
            if not parts:
                raise RequestValidationError(
                    f"messages[{index}].content has no usable parts",
                    code="invalid_messages",
                )
            normalized_messages.append({"role": role, "content": parts})
            continue
        raise RequestValidationError(
            f"messages[{index}].content must be a string or an array of parts",
            code="invalid_messages",
        )

    max_images = int(config.get("max_images_per_request", DEFAULT_CONFIG["max_images_per_request"]))
    if image_count > max_images:
        raise RequestValidationError(
            f"too many images in one request (limit {max_images})",
            code="too_many_images",
        )

    has_images = image_count > 0
    profile_key = resolve_profile_key(raw.get("model"), profiles, config, has_images)

    try:
        temperature = float(raw.get("temperature", 0.4))
    except (TypeError, ValueError):
        temperature = 0.4
    temperature = max(0.0, min(2.0, temperature))
    try:
        max_tokens = int(raw.get("max_tokens", raw.get("max_completion_tokens", 900)) or 900)
    except (TypeError, ValueError):
        max_tokens = 900
    max_tokens = max(1, min(8192, max_tokens))

    payload = {
        "model": profile_key,
        "messages": normalized_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    for option in OPTION_PASSTHROUGH:
        if option in raw:
            payload[option] = raw[option]

    return {
        "profile_key": profile_key,
        "payload": payload,
        "has_images": has_images,
        "stream": bool(raw.get("stream", False)),
    }


def build_completion_payload(request_id: str, profile_key: str, messages, text: str) -> dict:
    prompt_tokens = _estimate_tokens(_messages_text(messages))
    completion_tokens = _estimate_tokens(text)
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": profile_key,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def build_stream_chunk(request_id: str, profile_key: str, *, delta=None, finish_reason=None) -> dict:
    return {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": profile_key,
        "choices": [{
            "index": 0,
            "delta": dict(delta or {}),
            "finish_reason": finish_reason,
        }],
    }


def _default_completer_factory(profile_key: str):
    """Real wiring: run the request through the shared ManagedBackend so the
    OpenAI surface, Prompt Studio translation and task-agent nodes all share
    one loaded model and one lifecycle."""
    def complete(payload: dict, stream_callback=None) -> str:
        from . import api as prompt_studio_api  # lazy: avoids a circular import

        if prompt_studio_api._prompt_studio_worker_state()["alive"]:
            prompt_studio_api._stop_prompt_studio_private_worker()
        if prompt_studio_api._HY_MT_RUNTIME.status()["alive"]:
            prompt_studio_api._HY_MT_RUNTIME.stop()
        backend = prompt_studio_api._get_llm_translate_backend()
        backend.ensure_loaded(backend_profile=profile_key)
        return backend._complete_messages(payload, stream_callback=stream_callback)

    return complete


def _default_queue_state() -> dict:
    try:
        from . import api as prompt_studio_api

        return prompt_studio_api._prompt_studio_queue_state()
    except Exception:
        return {"busy": False, "running": 0, "pending": 0}


class OpenAICompatServer:
    """Request handlers; the backend is injected so tests can run a fake one."""

    def __init__(
        self,
        config: dict | None = None,
        profiles: dict | None = None,
        completer_factory=None,
        queue_state_fn=None,
        package_dir: Path | None = None,
    ):
        package_dir = Path(package_dir) if package_dir else DEFAULT_PACKAGE_DIR
        merged = dict(DEFAULT_CONFIG)
        if config is None:
            config = load_server_config(package_dir)
        for key in DEFAULT_CONFIG:
            if key in config:
                merged[key] = config[key]
        self.config = merged
        self.profiles = profiles if profiles is not None else load_backend_profiles(package_dir)
        self.completer_factory = completer_factory or _default_completer_factory
        self.queue_state_fn = queue_state_fn or _default_queue_state
        self._busy_lock = threading.Lock()
        self.root_v1_exposed = False

    # -- routing ---------------------------------------------------------

    @staticmethod
    def _registered_route_keys(routes) -> set:
        """Best-effort snapshot of (method, path) pairs already in the table.

        ComfyUI exposes a ``web.RouteTableDef`` whose duplicates only raise when
        the table is later added to the app, so bare /v1 conflicts must be
        detected by inspecting the table itself.
        """
        keys = set()
        try:
            for route_def in routes:
                method = str(getattr(route_def, "method", "") or "").upper()
                path = str(getattr(route_def, "path", "") or "")
                if method and path:
                    keys.add((method, path))
        except TypeError:
            pass
        return keys

    def attach(self, routes) -> None:
        routes.get(NAMESPACED_PREFIX + MODELS_PATH)(self.handle_models)
        routes.post(NAMESPACED_PREFIX + CHAT_COMPLETIONS_PATH)(self.handle_chat_completions)
        existing = self._registered_route_keys(routes)
        wanted = {("GET", MODELS_PATH), ("POST", CHAT_COMPLETIONS_PATH)}
        if existing and (existing & wanted):
            self.root_v1_exposed = False
            print(
                "[LocalOpenAIServer] bare /v1/* is already registered by another plugin; "
                f"use {NAMESPACED_PREFIX}/v1/* as the base URL instead",
                flush=True,
            )
            return
        try:
            routes.get(MODELS_PATH)(self.handle_models)
            routes.post(CHAT_COMPLETIONS_PATH)(self.handle_chat_completions)
            self.root_v1_exposed = True
        except RuntimeError:
            # UrlDispatcher-style routers raise here instead; degrade the same way.
            self.root_v1_exposed = False
            print(
                "[LocalOpenAIServer] bare /v1/* is unavailable; "
                f"use {NAMESPACED_PREFIX}/v1/* as the base URL instead",
                flush=True,
            )

    # -- guards ------------------------------------------------------------

    def _guard_request(self, request) -> web.Response | None:
        if not resolve_enabled(self.config):
            return openai_error_response(
                "the local OpenAI-compatible server is disabled; enable it in "
                "config/local_openai_server.local.json",
                403,
                "permission_error",
                code="server_disabled",
            )
        if not remote_is_loopback(request.remote):
            return openai_error_response(
                "the local OpenAI-compatible server only accepts loopback connections",
                403,
                "permission_error",
                code="loopback_only",
            )
        return None

    def _queue_guard_response(self) -> web.Response | None:
        if not bool(self.config.get("queue_guard", True)):
            return None
        try:
            state = self.queue_state_fn() or {}
        except Exception:
            return None
        if not state.get("busy"):
            return None
        return openai_error_response(
            "ComfyUI queue is running or waiting; the local LLM server is paused so it "
            "can never block image generation",
            409,
            "server_error",
            code="comfy_queue_busy",
        )

    # -- endpoints ---------------------------------------------------------

    async def handle_models(self, request) -> web.Response:
        guard = self._guard_request(request)
        if guard is not None:
            return guard
        return web.json_response(build_models_payload(self.profiles))

    async def handle_chat_completions(self, request) -> web.StreamResponse:
        guard = self._guard_request(request)
        if guard is not None:
            return guard

        max_body = int(self.config.get("max_request_bytes", DEFAULT_CONFIG["max_request_bytes"]))
        if request.content_length is not None and request.content_length > max_body:
            return openai_error_response(
                f"request body exceeds the {max_body}-byte limit",
                413,
                "invalid_request_error",
                code="request_too_large",
            )
        try:
            body = await request.content.read(max_body + 1)
        except Exception as error:
            return openai_error_response(f"failed to read request body: {error}", 400, "invalid_request_error", code="invalid_body")
        if len(body) > max_body:
            return openai_error_response(
                f"request body exceeds the {max_body}-byte limit",
                413,
                "invalid_request_error",
                code="request_too_large",
            )
        try:
            raw = json.loads(body.decode("utf-8"))
        except Exception as error:
            return openai_error_response(f"request body is not valid JSON: {error}", 400, "invalid_request_error", code="invalid_json")

        try:
            normalized = normalize_chat_payload(raw, self.profiles, self.config)
        except RequestValidationError as error:
            return openai_error_response(error.message, error.status, error.err_type, code=error.code, param=error.param)

        queue_error = self._queue_guard_response()
        if queue_error is not None:
            return queue_error

        if not self._busy_lock.acquire(blocking=False):
            return openai_error_response(
                "another local LLM request is still running; this server is single-flight",
                429,
                "rate_limit_error",
                code="server_busy",
            )

        completer = self.completer_factory(normalized["profile_key"])
        try:
            if normalized["stream"]:
                return await self._respond_stream(request, normalized, completer)
            return await self._respond_json(normalized, completer)
        except Exception:
            # Lock ownership has already moved to the worker's done-callback on
            # the normal paths; release here only when the worker never started.
            raise

    # -- completion runners --------------------------------------------------

    def _release_busy(self, _future=None) -> None:
        try:
            self._busy_lock.release()
        except RuntimeError:
            pass

    async def _respond_json(self, normalized: dict, completer) -> web.Response:
        loop = asyncio.get_running_loop()
        timeout = max(1, int(self.config.get("request_timeout_sec", 600)))
        request_id = "chatcmpl-" + uuid.uuid4().hex[:24]
        try:
            future = loop.run_in_executor(None, completer, normalized["payload"], None)
        except Exception as error:
            self._release_busy()
            return openai_error_response(f"failed to start completion: {error}", 500, "server_error", code="completion_start_failed")
        future.add_done_callback(self._release_busy)
        try:
            text = await asyncio.wait_for(asyncio.shield(future), timeout)
        except asyncio.TimeoutError:
            # The worker keeps running and still owns the busy lock; the client
            # gets a clean timeout instead of a hung connection.
            return openai_error_response(
                f"completion timed out after {timeout}s; the backend is still finishing "
                "in the background and will free the slot when done",
                504,
                "server_error",
                code="completion_timeout",
            )
        except Exception as error:
            return openai_error_response(f"backend completion failed: {error}", 502, "server_error", code="backend_error")
        return web.json_response(
            build_completion_payload(request_id, normalized["profile_key"], normalized["payload"]["messages"], str(text or ""))
        )

    async def _respond_stream(self, request, normalized: dict, completer) -> web.StreamResponse:
        loop = asyncio.get_running_loop()
        timeout = max(1, int(self.config.get("request_timeout_sec", 600)))
        request_id = "chatcmpl-" + uuid.uuid4().hex[:24]
        events: thread_queue.Queue = thread_queue.Queue()

        def run_completion():
            try:
                text = completer(normalized["payload"], lambda delta: events.put(("delta", str(delta))))
                events.put(("done", str(text or "")))
            except Exception as error:
                events.put(("error", error))

        try:
            future = loop.run_in_executor(None, run_completion)
        except Exception as error:
            self._release_busy()
            return openai_error_response(f"failed to start completion: {error}", 500, "server_error", code="completion_start_failed")
        future.add_done_callback(self._release_busy)

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        async def write_sse(payload_text: str) -> bool:
            try:
                await response.write(f"data: {payload_text}\n\n".encode("utf-8"))
                return True
            except (ConnectionError, RuntimeError, asyncio.CancelledError):
                return False

        role_chunk = build_stream_chunk(request_id, normalized["profile_key"], delta={"role": "assistant", "content": ""})
        if not await write_sse(json.dumps(role_chunk, ensure_ascii=False)):
            return response

        deadline = time.monotonic() + timeout
        terminal_sent = False
        while True:
            try:
                kind, value = events.get_nowait()
            except thread_queue.Empty:
                if time.monotonic() > deadline:
                    error_payload = openai_error_payload(
                        f"completion timed out after {timeout}s; the backend is still finishing "
                        "in the background and will free the slot when done",
                        "server_error",
                        code="completion_timeout",
                    )
                    await write_sse(json.dumps(error_payload, ensure_ascii=False))
                    await write_sse("[DONE]")
                    terminal_sent = True
                    break
                await asyncio.sleep(0.05)
                continue

            if kind == "delta":
                chunk = build_stream_chunk(request_id, normalized["profile_key"], delta={"content": value})
                if not await write_sse(json.dumps(chunk, ensure_ascii=False)):
                    return response
                continue

            if kind == "error":
                error_payload = openai_error_payload(
                    f"backend completion failed: {value}",
                    "server_error",
                    code="backend_error",
                )
                await write_sse(json.dumps(error_payload, ensure_ascii=False))
                await write_sse("[DONE]")
                terminal_sent = True
                break

            # kind == "done": emit the terminal chunk with finish_reason.
            final_chunk = build_stream_chunk(request_id, normalized["profile_key"], delta={}, finish_reason="stop")
            await write_sse(json.dumps(final_chunk, ensure_ascii=False))
            await write_sse("[DONE]")
            terminal_sent = True
            break

        if not terminal_sent:
            await write_sse("[DONE]")
        try:
            await response.write_eof()
        except Exception:
            pass
        return response


def register_openai_compat_routes(routes, package_dir: Path | None = None) -> OpenAICompatServer:
    """Attach the local OpenAI-compatible endpoints to the ComfyUI aiohttp
    router. Registration itself is cheap and unconditional; the enabled flag
    is evaluated per request so users can toggle it without a restart."""
    server = OpenAICompatServer(package_dir=package_dir)
    server.attach(routes)
    print(
        "[LocalOpenAIServer] routes registered "
        f"(namespaced={NAMESPACED_PREFIX}/v1, root_v1={'yes' if server.root_v1_exposed else 'no'}, "
        f"enabled={resolve_enabled(server.config)})",
        flush=True,
    )
    return server
