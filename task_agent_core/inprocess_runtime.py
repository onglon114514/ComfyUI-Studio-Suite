from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskAgentTaskRequest:
    task_type: str
    inputs: dict[str, Any] = field(default_factory=dict)
    temperature: float = 0.7
    max_tokens: int = 1200
    model_source: str = "profile_catalog"
    backend_profile: str = ""
    context_size: int = 16384
    custom_model_path: str = ""
    custom_mmproj_path: str = ""
    auto_load_backend: bool = True
    unload_after_run: bool = False
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskAgentTaskResponse:
    raw_text: str = ""
    json_text: str = ""
    parsed: dict[str, Any] | None = None
    status: str = "success"
    provider_key: str = ""
    model_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class InProcessTaskRuntime:
    provider_key = "unknown"

    def run_task(self, request: TaskAgentTaskRequest) -> TaskAgentTaskResponse:
        raise NotImplementedError

    def unload(self) -> None:
        return None

    def status(self) -> dict[str, Any]:
        return {
            "provider_key": self.provider_key,
            "loaded": False,
        }
