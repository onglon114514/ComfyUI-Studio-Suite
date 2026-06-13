from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InProcessProviderSpec:
    key: str
    display_name: str
    execution_mode: str
    priority: int = 100
    release_status: str = "planned"
    supports_text: bool = True
    supports_vision: bool = False
    requires_external_service: bool = False
    reuses_generation_capable_clip: bool = False
    notes: str = ""


INPROCESS_PROVIDER_SPECS = {
    "clip_reuse": InProcessProviderSpec(
        key="clip_reuse",
        display_name="Reusable CLIP-side LLM",
        execution_mode="inprocess",
        priority=30,
        release_status="compatibility_path",
        supports_text=True,
        supports_vision=True,
        reuses_generation_capable_clip=True,
        notes=(
            "Compatibility path only. For pipelines where the so-called text encoder is "
            "actually a reusable LLM with generation capability, such as NewBie Gemma/"
            "Qwen-style designs. Not intended as the main performance-oriented runtime."
        ),
    ),
    "transformers_inproc": InProcessProviderSpec(
        key="transformers_inproc",
        display_name="Transformers In-Process",
        execution_mode="inprocess",
        priority=10,
        release_status="primary_target",
        supports_text=True,
        supports_vision=True,
        notes=(
            "Loads a HuggingFace-compatible generation model directly inside the ComfyUI "
            "Python process. Intended as one of the main local task execution paths without "
            "an external backend window."
        ),
    ),
    "llama_cpp_python_inproc": InProcessProviderSpec(
        key="llama_cpp_python_inproc",
        display_name="llama-cpp-python In-Process",
        execution_mode="inprocess",
        priority=20,
        release_status="primary_target",
        supports_text=True,
        supports_vision=True,
        notes=(
            "Runs GGUF models directly inside the ComfyUI Python process through "
            "llama-cpp-python. Intended as one of the main no-extra-window local runtime paths."
        ),
    ),
}


def get_known_inprocess_provider_specs():
    return dict(INPROCESS_PROVIDER_SPECS)


def get_inprocess_provider_spec(provider_key: str):
    return INPROCESS_PROVIDER_SPECS.get(str(provider_key or "").strip().lower())


def get_inprocess_provider_priority(provider_key: str, default: int = 999) -> int:
    spec = get_inprocess_provider_spec(provider_key)
    return spec.priority if spec else default


def get_inprocess_provider_release_status(provider_key: str, default: str = "unknown") -> str:
    spec = get_inprocess_provider_spec(provider_key)
    return spec.release_status if spec else default


def provider_is_known_inprocess(provider_key: str) -> bool:
    return get_inprocess_provider_spec(provider_key) is not None


def provider_reuses_generation_capable_clip(provider_key: str) -> bool:
    spec = get_inprocess_provider_spec(provider_key)
    return bool(spec and spec.reuses_generation_capable_clip)


def provider_is_inprocess_runtime(provider_key: str) -> bool:
    spec = get_inprocess_provider_spec(provider_key)
    return bool(spec and spec.execution_mode == "inprocess")


def get_recommended_inprocess_provider_order():
    return [
        spec.key
        for spec in sorted(
            INPROCESS_PROVIDER_SPECS.values(),
            key=lambda spec: (spec.priority, spec.display_name.lower()),
        )
    ]
