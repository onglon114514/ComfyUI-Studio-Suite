"""Studio Suite Set/Get nodes + server-side prompt expansion.

Why this exists
---------------
ComfyUI classic "Set Node"/"Get Node" are frontend virtual nodes: they are never
registered as backend node classes, so a workflow that uses them cannot be run by
ComfyUI's own executor and therefore cannot be dry-run on the canvas. The
graph->prompt converter (graph_to_prompt.py) emulates that resolution only for the
Studio Suite / webUI path.

This module adds a real backend pair so the same workflow runs natively:

* StudioSuiteSet - pass-through. Carries a name label and forwards its (wildcard)
  value input to its output, so it is a valid, executable node.
* StudioSuiteGet - name-based reference, resolved a moment before execution by a
  /prompt hook that rewrites every consumer of the Get node to read directly from
  the matching Set node's source; the Get node is then removed from the prompt.

With the hook installed, submitting through ComfyUI Queue produces the same result
as the webUI converter path - so a workflow author can debug it inside ComfyUI
before wiring it into webUI. Existing converter behaviour is untouched.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple


class _AnyType(str):
    """Wildcard socket type (never equal to anything, so any link is accepted)."""

    def __ne__(self, other):  # noqa: D105
        return False


ANY = _AnyType("*")

# Types treated as Set/Get by both this module and graph_to_prompt.py.
SET_TYPES = {"StudioSuiteSet", "SetNode", "easy setNode"}
GET_TYPES = {"StudioSuiteGet", "GetNode", "easy getNode"}

_NAME_KEYS = ("name", "Constant")


class StudioSuiteSetNode:
    """Named pass-through; the canonical definition point of a value."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"name": ("STRING", {"default": ""})},
            "optional": {"value": (ANY, {})},
        }

    RETURN_TYPES = (ANY,)
    RETURN_NAMES = ("value",)
    FUNCTION = "apply"
    CATEGORY = "Studio Suite/SetGet"

    def apply(self, name: str = "", value: Any = None):
        return (value,)


class StudioSuiteGetNode:
    """Name-based reference to a StudioSuiteSetNode.

    Resolved at prompt-submission time; if the hook is not installed the node
    simply returns fallback.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"name": ("STRING", {"default": ""})},
            "optional": {"fallback": (ANY, {})},
        }

    RETURN_TYPES = (ANY,)
    RETURN_NAMES = ("value",)
    FUNCTION = "apply"
    CATEGORY = "Studio Suite/SetGet"

    def apply(self, name: str = "", fallback: Any = None):
        return (fallback,)


def _input_name(node: Dict[str, Any]) -> str:
    inputs = node.get("inputs") or {}
    for key in _NAME_KEYS:
        if key in inputs:
            raw = inputs[key]
            if isinstance(raw, (str, int, float)) and str(raw).strip():
                return str(raw).strip()
    return ""


def _source_link(node: Dict[str, Any]) -> Optional[Tuple[str, int]]:
    inputs = node.get("inputs") or {}
    for key, val in inputs.items():
        if key in _NAME_KEYS:
            continue
        if isinstance(val, (list, tuple)) and len(val) == 2 and isinstance(val[0], (str, int)):
            try:
                return (str(val[0]), int(val[1]))
            except (TypeError, ValueError):
                continue
    return None


def expand_setget(prompt: Dict[str, Any]) -> List[str]:
    """Rewrite StudioSuiteGet references in-place to point at their Set source.

    prompt is ComfyUI API-format: {node_id: {"class_type", "inputs"}}.
    Returns warnings; never raises on malformed input.
    """
    warnings: List[str] = []
    if not isinstance(prompt, dict):
        return ["prompt is not a dict; nothing to expand"]

    set_sources: Dict[str, Tuple[str, int]] = {}
    get_nodes: Dict[str, str] = {}

    for nid, node in prompt.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type", ""))
        if class_type in SET_TYPES:
            name = _input_name(node)
            if not name:
                continue
            src = _source_link(node)
            if src is None:
                warnings.append("Set %r has no input link (constant); skipped" % name)
                continue
            set_sources.setdefault(name, src)
        elif class_type in GET_TYPES:
            name = _input_name(node)
            if name:
                get_nodes[str(nid)] = name

    resolutions: Dict[str, Tuple[str, int]] = {}
    for gid, name in get_nodes.items():
        if name in set_sources:
            resolutions[gid] = set_sources[name]
        else:
            warnings.append("Get node %s references %r but no matching Set was found" % (gid, name))

    if not resolutions:
        return warnings

    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key, val in list(inputs.items()):
            if isinstance(val, (list, tuple)) and len(val) == 2 and str(val[0]) in resolutions:
                src_node, src_slot = resolutions[str(val[0])]
                inputs[key] = [src_node, src_slot]

    for gid in resolutions:
        prompt.pop(gid, None)

    _drop_unreferenced_set_nodes(prompt)
    return warnings


def _referenced_nodes(prompt: Dict[str, Any]) -> set:
    refs = set()
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        for val in (node.get("inputs") or {}).values():
            if isinstance(val, (list, tuple)) and len(val) == 2 and isinstance(val[0], (str, int)):
                refs.add(str(val[0]))
    return refs


def _drop_unreferenced_set_nodes(prompt: Dict[str, Any]) -> None:
    referenced = _referenced_nodes(prompt)
    for nid in list(prompt.keys()):
        node = prompt.get(nid)
        if isinstance(node, dict) and str(node.get("class_type", "")) in SET_TYPES and str(nid) not in referenced:
            prompt.pop(nid, None)


def _setget_prompt_handler(json_data):
    try:
        if isinstance(json_data, dict) and isinstance(json_data.get("prompt"), dict):
            for warning in expand_setget(json_data["prompt"]):
                logging.info("[studio-suite][setget] %s", warning)
    except Exception:
        logging.warning("[studio-suite] Set/Get expansion failed", exc_info=True)
    return json_data


def _try_register_setget_hook() -> bool:
    try:
        from server import PromptServer  # type: ignore
    except Exception:
        return False
    server = getattr(PromptServer, "instance", None)
    if server is None:
        return False
    try:
        server.add_on_prompt_handler(_setget_prompt_handler)
    except Exception:
        logging.warning("[studio-suite] could not register Set/Get prompt hook", exc_info=True)
        return False
    return True


def install_setget_prompt_hook() -> bool:
    """Register the expansion hook with ComfyUI PromptServer (best effort).

    PromptServer.instance does not exist yet while custom nodes are imported at
    startup, so a failed immediate registration is retried on a short-lived
    daemon thread.
    """
    if _try_register_setget_hook():
        return True

    import threading
    import time

    def _retry():
        for _ in range(120):
            time.sleep(0.5)
            if _try_register_setget_hook():
                logging.info("[studio-suite] Set/Get prompt hook registered (deferred)")
                return
        logging.info("[studio-suite] Set/Get prompt hook not installed (PromptServer unavailable)")

    threading.Thread(target=_retry, name="studio-suite-setget-hook", daemon=True).start()
    return False


NODE_CLASS_MAPPINGS = {
    "StudioSuiteSet": StudioSuiteSetNode,
    "StudioSuiteGet": StudioSuiteGetNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "StudioSuiteSet": "Studio Suite \u00b7 \u8bbe\u7f6e\u70b9 (Set)",
    "StudioSuiteGet": "Studio Suite \u00b7 \u83b7\u53d6\u70b9 (Get)",
}

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "expand_setget",
    "install_setget_prompt_hook",
    "SET_TYPES",
    "GET_TYPES",
]
