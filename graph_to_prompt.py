"""graph_to_prompt.py – ComfyUI graph (canvas) format → API prompt format.

Supports bypass (mode=4), mute (mode=2), group-based toggling via
``disabled_groups``, and **native subgraph expansion** (ComfyUI ≥0.36).
Widget→input-name mapping is resolved from the running ComfyUI instance's
``/object_info`` endpoint (cached).
"""

from __future__ import annotations

import json
import re
import urllib.request
from functools import lru_cache
from typing import Any, Dict, List, Optional, Set, Tuple

__all__ = ["graph_to_prompt", "list_groups"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODE_BYPASS = 4
_MODE_MUTE = 2

# Link types that are considered "connection" slots rather than widgets.
_LINK_TYPE_NAMES = {"*", "ANY"}

# Widget type names in ComfyUI subgraph definitions
_WIDGET_TYPE_NAMES = {"INT", "FLOAT", "STRING", "COMBO", "BOOLEAN", "NUMBER"}

# Special node IDs used inside subgraph definitions
_SUBGRAPH_INPUT_STUB = -10   # origin_id for subgraph input links
_SUBGRAPH_OUTPUT_STUB = -20  # target_id for subgraph output links

# Set/Get virtual node types (frontend-only, not in /object_info)
_SET_NODE_TYPES = {"SetNode", "easy setNode", "StudioSuiteSet"}
_GET_NODE_TYPES = {"GetNode", "easy getNode", "StudioSuiteGet"}

# Pure frontend UI nodes that should be silently skipped (no warning)
_FRONTEND_UI_TYPES = {"Fast Groups Bypasser (rgthree)", "Image Comparer (rgthree)"}


# ---------------------------------------------------------------------------
# object_info helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _fetch_object_info(url: str = "http://127.0.0.1:8188/object_info") -> Dict[str, Any]:
    """Fetch and cache the full /object_info payload."""
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_link_slot(type_spec: Any) -> bool:
    """Return True if the type spec represents a connection slot (not a widget)."""
    if isinstance(type_spec, str):
        return type_spec.upper() in _LINK_TYPE_NAMES
    if isinstance(type_spec, (list, tuple)):
        if len(type_spec) == 1 and isinstance(type_spec[0], str):
            return type_spec[0].upper() in _LINK_TYPE_NAMES
        return False
    return False


@lru_cache(maxsize=None)
def _widget_names_for_class(class_type: str, object_info_url: str) -> List[str]:
    """Return ordered list of widget input names for a class_type."""
    try:
        info = _fetch_object_info(object_info_url)
    except Exception:
        return []
    cls_info = info.get(class_type)
    if not cls_info:
        return []
    inputs = cls_info.get("input") or {}
    names: List[str] = []
    for section in ("required", "optional"):
        section_data = inputs.get(section) or {}
        for name, spec in section_data.items():
            if _is_link_slot(spec):
                continue
            if isinstance(spec, (list, tuple)) and len(spec) >= 2:
                opts = spec[1]
                if isinstance(opts, dict) and opts.get("hidden"):
                    continue
            names.append(name)
    return names


# ---------------------------------------------------------------------------
# Group helpers
# ---------------------------------------------------------------------------


def _node_fully_inside(node: Dict[str, Any], bounding: List[float]) -> bool:
    """Check whether the *entire* node rectangle is inside the group bounding box."""
    pos = node.get("pos") or [0, 0]
    size = node.get("size") or [0, 0]
    bx, by, bw, bh = bounding
    return (
        pos[0] >= bx
        and pos[1] >= by
        and pos[0] + size[0] <= bx + bw
        and pos[1] + size[1] <= by + bh
    )


def _bounding_inside(inner: List[float], outer: List[float]) -> bool:
    """Check whether *inner* bounding box is fully inside *outer*."""
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    return ix >= ox and iy >= oy and ix + iw <= ox + ow and iy + ih <= oy + oh


def list_groups(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return group metadata including nested parent-child relationships.

    Each entry: ``{"title", "bounding", "node_ids", "parent"}``.
    """
    nodes = graph.get("nodes") or []
    groups = graph.get("groups") or []

    records: List[Dict[str, Any]] = []
    for g in groups:
        bounding = g.get("bounding") or [0, 0, 0, 0]
        node_ids = [
            n["id"]
            for n in nodes
            if _node_fully_inside(n, bounding)
        ]
        records.append(
            {
                "title": g.get("title", ""),
                "bounding": bounding,
                "node_ids": node_ids,
                "parent": None,
            }
        )

    for i, rec in enumerate(records):
        best_idx = -1
        best_area = float("inf")
        for j, other in enumerate(records):
            if i == j:
                continue
            if _bounding_inside(rec["bounding"], other["bounding"]):
                ox, oy, ow, oh = other["bounding"]
                area = ow * oh
                if area < best_area:
                    best_area = area
                    best_idx = j
        if best_idx >= 0:
            rec["parent"] = records[best_idx]["title"]

    return records


# ---------------------------------------------------------------------------
# Link parsing
# ---------------------------------------------------------------------------


def _parse_link(link: Any) -> Tuple[int, int, int, int, int, str]:
    """Normalise a link entry (array or dict) to a tuple."""
    if isinstance(link, dict):
        return (
            int(link.get("id", 0)),
            int(link.get("origin_id", 0)),
            int(link.get("origin_slot", 0)),
            int(link.get("target_id", 0)),
            int(link.get("target_slot", 0)),
            str(link.get("type", "")),
        )
    return (
        int(link[0]),
        int(link[1]),
        int(link[2]),
        int(link[3]),
        int(link[4]),
        str(link[5]) if len(link) > 5 else "",
    )


# ---------------------------------------------------------------------------
# Subgraph definition helpers
# ---------------------------------------------------------------------------


def _build_subgraph_map(graph: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Build a map from subgraph definition ID to its definition dict."""
    definitions = graph.get("definitions") or {}
    if not isinstance(definitions, dict):
        return {}
    subgraphs = definitions.get("subgraphs") or []
    result: Dict[str, Dict[str, Any]] = {}
    for sg in subgraphs:
        sg_id = sg.get("id")
        if sg_id:
            result[str(sg_id)] = sg
    return result


def _subgraph_input_targets(sg: Dict[str, Any]) -> Dict[int, List[Tuple[int, int]]]:
    """Map subgraph input slot index → list of (internal_node_id, internal_slot).

    A single subgraph input may fan-out to multiple internal nodes.
    """
    result: Dict[int, List[Tuple[int, int]]] = {}
    links = sg.get("links") or []
    inputs = sg.get("inputs") or []
    for slot_idx, inp in enumerate(inputs):
        targets: List[Tuple[int, int]] = []
        for lid in inp.get("linkIds") or []:
            link = next((l for l in links if l.get("id") == lid), None)
            if link and link.get("origin_id") == _SUBGRAPH_INPUT_STUB:
                targets.append((int(link["target_id"]), int(link["target_slot"])))
        result[slot_idx] = targets
    return result


def _subgraph_output_sources(sg: Dict[str, Any]) -> Dict[int, List[Tuple[int, int]]]:
    """Map subgraph output slot index → list of (internal_node_id, internal_slot).

    Usually a single source, but we collect all for completeness.
    """
    result: Dict[int, List[Tuple[int, int]]] = {}
    links = sg.get("links") or []
    outputs = sg.get("outputs") or []
    for slot_idx, out in enumerate(outputs):
        sources: List[Tuple[int, int]] = []
        for lid in out.get("linkIds") or []:
            link = next((l for l in links if l.get("id") == lid), None)
            if link and link.get("target_id") == _SUBGRAPH_OUTPUT_STUB:
                sources.append((int(link["origin_id"]), int(link["origin_slot"])))
        result[slot_idx] = sources
    return result


# ---------------------------------------------------------------------------
# Set/Get name normalisation
# ---------------------------------------------------------------------------


def _normalise_setget_name(raw: str) -> str:
    """Strip decorative prefixes from a Set/Get node name.

    Examples:
        "➡️c seed"      → "c seed"
        "⬅️z a正面tag"   → "z a正面tag"
        "a 自图像加载"    → "a 自图像加载"
        "Set_a 自图像加载" → "a 自图像加载"  (also strip Set_/Get_ prefix)
    """
    name = raw.strip()
    # Strip Set_/Get_ prefix (used by KJNodes)
    if name.startswith("Set_"):
        name = name[4:]
    elif name.startswith("Get_"):
        name = name[4:]
    # Strip arrow emojis and other decorative prefixes at the start
    # Common patterns: ➡️, ⬅️, and other emoji/variation selectors
    name = re.sub(r'^[\u2190-\u21FF\u2B00-\u2BFF\uFE0F\u200D\s]+', '', name)
    # Also strip any remaining variation selectors or ZWJ
    name = re.sub(r'[\uFE0F\u200D]', '', name)
    return name.strip()


# Default titles that indicate "no real name" (user never typed one)
_DEFAULT_SETGET_TITLES = {"设置点", "获取点", "Set Node", "Get Node", "Set", "Get", ""}


def _get_setget_name(node: Dict[str, Any]) -> str:
    """Extract the canonical name from a Set/Get node.

    Priority:
    1. widgets_values[0] (the "Constant" widget) — if non-empty
    2. widgets_values_named.Constant — if non-empty
    3. title with decoration stripped — if it is not a default placeholder
    4. "" (caller should use proximity fallback)
    """
    widgets = node.get("widgets_values") or []
    if widgets:
        val = str(widgets[0]).strip()
        if val:
            return val

    named = node.get("widgets_values_named") or {}
    if "Constant" in named:
        val = str(named["Constant"]).strip()
        if val:
            return val

    title = node.get("title", "")
    if title:
        stripped = _normalise_setget_name(title)
        # Reject default placeholder titles
        if stripped and stripped not in _DEFAULT_SETGET_TITLES:
            return stripped

    return ""


# ---------------------------------------------------------------------------
# Core converter
# ---------------------------------------------------------------------------


class _GraphToPrompt:
    def __init__(
        self,
        graph: Dict[str, Any],
        object_info: Optional[Dict[str, Any]] = None,
        object_info_url: str = "http://127.0.0.1:8188/object_info",
        disabled_groups: Optional[List[str]] = None,
    ):
        self.graph = graph
        self.object_info_url = object_info_url
        self.warnings: List[str] = []

        if object_info is not None:
            self._object_info_override = object_info
        else:
            self._object_info_override = None

        # Build subgraph definition map
        self._subgraph_map = _build_subgraph_map(graph)

        # Track disabled subgraph names
        self._disabled_subgraph_names: Set[str] = set()
        if disabled_groups:
            for name in disabled_groups:
                self._disabled_subgraph_names.add(name)

        # Main graph nodes and links
        self.nodes: Dict[int, Dict[str, Any]] = {
            int(n["id"]): n for n in (graph.get("nodes") or [])
        }
        self.links: Dict[int, Tuple[int, int, int, int, int, str]] = {}
        for raw in graph.get("links") or []:
            lid, onode, oslot, tnode, tslot, ltype = _parse_link(raw)
            self.links[lid] = (lid, onode, oslot, tnode, tslot, ltype)

        # Group-bypass set (existing logic)
        self._group_bypass_node_ids: set[int] = set()
        if disabled_groups:
            group_records = list_groups(graph)
            disabled_set = set(disabled_groups)

            def _is_disabled(title: str, _depth: int = 0) -> bool:
                if _depth > 50:
                    return False
                for rec in group_records:
                    if rec["title"] == title:
                        if title in disabled_set:
                            return True
                        parent = rec.get("parent")
                        if parent:
                            return _is_disabled(parent, _depth + 1)
                        return False
                return False

            for rec in group_records:
                if _is_disabled(rec["title"]):
                    self._group_bypass_node_ids.update(rec["node_ids"])

        # Cache for resolved node prompts
        self._resolved: Dict[str, Optional[Dict[str, Any]]] = {}
        self._resolving: set[str] = set()  # cycle detection

        # Subgraph expansion tracking
        self._expanded_subgraph_count = 0
        self._subgraph_bypass_cut_count = 0

        # Set/Get resolution maps
        # canonical_name → list of set node_ids that define it
        self._set_nodes: Dict[str, List[int]] = {}
        # get_node_id → canonical_name
        self._get_nodes: Dict[int, str] = {}
        # Build the maps
        self._build_setget_maps()

    # -- Set/Get helpers ----------------------------------------------------

    def _build_setget_maps(self) -> None:
        """Scan all top-level nodes and index Set/Get nodes by canonical name.

        Nodes with empty names are paired by proximity (nearest Set↔Get).
        """
        # First pass: collect nodes with valid names
        unnamed_sets: List[int] = []
        unnamed_gets: List[int] = []

        for nid, node in self.nodes.items():
            ntype = str(node.get("type", ""))
            if ntype in _SET_NODE_TYPES:
                name = _get_setget_name(node)
                if name:
                    self._set_nodes.setdefault(name, []).append(nid)
                else:
                    unnamed_sets.append(nid)
            elif ntype in _GET_NODE_TYPES:
                name = _get_setget_name(node)
                if name:
                    self._get_nodes[nid] = name
                else:
                    unnamed_gets.append(nid)

        # Second pass: proximity pairing for unnamed nodes
        if unnamed_sets or unnamed_gets:
            self._proximity_pair(unnamed_sets, unnamed_gets)

        # Warn about duplicate Set definitions
        for name, ids in self._set_nodes.items():
            if len(ids) > 1:
                self.warnings.append(
                    f"Multiple Set nodes define '{name}': {ids}; using first."
                )

    def _proximity_pair(self, unnamed_sets: List[int], unnamed_gets: List[int]) -> None:
        """Pair unnamed Set/Get nodes by spatial proximity.

        For each unnamed Get, find the nearest unnamed Set (by pos distance).
        If the distance is within a reasonable threshold, assign them the same
        synthetic name and emit an informational warning.
        """
        if not unnamed_sets or not unnamed_gets:
            # If only one side is unnamed, we cannot pair — leave them unresolved
            for nid in unnamed_sets:
                self.warnings.append(
                    f"Set node {nid} has no resolvable name and no unnamed Get to pair with."
                )
            for nid in unnamed_gets:
                self.warnings.append(
                    f"Get node {nid} has no resolvable name and no unnamed Set to pair with."
                )
            return

        # Maximum distance (in canvas units) for proximity pairing
        _MAX_PAIR_DISTANCE = 500.0

        # Build a synthetic name for each pair
        pair_counter = 0
        used_sets: Set[int] = set()

        for get_id in unnamed_gets:
            get_node = self.nodes.get(get_id)
            if not get_node:
                continue
            gx, gy = get_node.get("pos") or [0, 0]

            best_set: Optional[int] = None
            best_dist = float("inf")
            for set_id in unnamed_sets:
                if set_id in used_sets:
                    continue
                set_node = self.nodes.get(set_id)
                if not set_node:
                    continue
                sx, sy = set_node.get("pos") or [0, 0]
                dist = ((gx - sx) ** 2 + (gy - sy) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best_set = set_id

            if best_set is not None and best_dist <= _MAX_PAIR_DISTANCE:
                pair_counter += 1
                synth_name = f"__proximity_pair_{pair_counter}"
                used_sets.add(best_set)
                self._set_nodes.setdefault(synth_name, []).append(best_set)
                self._get_nodes[get_id] = synth_name
                self.warnings.append(
                    f"Set node {best_set} and Get node {get_id} have no names; "
                    f"paired by proximity (distance={best_dist:.1f})."
                )
            else:
                self.warnings.append(
                    f"Get node {get_id} has no resolvable name and no nearby unnamed Set to pair with."
                )

        # Any remaining unnamed Sets are unresolvable
        for set_id in unnamed_sets:
            if set_id not in used_sets:
                self.warnings.append(
                    f"Set node {set_id} has no resolvable name and was not paired."
                )

    def _is_set_node(self, node: Dict[str, Any]) -> bool:
        return str(node.get("type", "")) in _SET_NODE_TYPES

    def _is_get_node(self, node: Dict[str, Any]) -> bool:
        return str(node.get("type", "")) in _GET_NODE_TYPES

    def _is_frontend_ui(self, node: Dict[str, Any]) -> bool:
        return str(node.get("type", "")) in _FRONTEND_UI_TYPES

    def _find_set_source(self, get_node_id: int) -> Optional[Tuple[int, int]]:
        """Find the data source for a Get node.

        Returns (origin_node_id, origin_slot) or None if unresolvable.
        """
        name = self._get_nodes.get(get_node_id)
        if name is None:
            return None

        set_ids = self._set_nodes.get(name)
        if not set_ids:
            self.warnings.append(
                f"Get node {get_node_id} references '{name}' but no matching Set node found."
            )
            return None

        set_id = set_ids[0]  # use first, duplicate warning already emitted
        set_node = self.nodes.get(set_id)
        if set_node is None:
            return None

        # The Set node's input link is the data source
        inputs = set_node.get("inputs") or []
        for inp in inputs:
            if inp.get("link") is not None:
                link_id = int(inp["link"])
                link = self.links.get(link_id)
                if link:
                    return (link[1], link[2])

        # Set node has no input link — check widget value (constant Set)
        widgets = set_node.get("widgets_values") or []
        if widgets:
            # This is a constant value, not a link — return a special marker
            # We'll handle this by returning the set node itself with slot -1
            return (set_id, -1)

        return None

    # -- object_info access -------------------------------------------------

    def _widget_names(self, class_type: str) -> List[str]:
        if self._object_info_override is not None:
            info = self._object_info_override
            cls_info = info.get(class_type)
            if not cls_info:
                return []
            inputs = cls_info.get("input") or {}
            names: List[str] = []
            for section in ("required", "optional"):
                for name, spec in (inputs.get(section) or {}).items():
                    if _is_link_slot(spec):
                        continue
                    if isinstance(spec, (list, tuple)) and len(spec) >= 2:
                        opts = spec[1]
                        if isinstance(opts, dict) and opts.get("hidden"):
                            continue
                    names.append(name)
            return names
        return _widget_names_for_class(class_type, self.object_info_url)

    def _class_type_exists(self, class_type: str) -> bool:
        """Check if a class_type exists in object_info."""
        if self._object_info_override is not None:
            return class_type in self._object_info_override
        try:
            info = _fetch_object_info(self.object_info_url)
            return class_type in info
        except Exception:
            return True  # assume exists if we can't check

    # -- mode helpers -------------------------------------------------------

    def _effective_mode(self, node: Dict[str, Any], node_id: Optional[int] = None) -> int:
        """Return the effective mode, considering group bypass."""
        nid = node_id if node_id is not None else int(node.get("id", 0))
        if nid in self._group_bypass_node_ids:
            return _MODE_BYPASS
        return int(node.get("mode", 0))

    # -- bypass resolution --------------------------------------------------

    def _find_bypass_source(
        self, node: Dict[str, Any], output_slot: int, output_type: str
    ) -> Optional[Tuple[int, int]]:
        """For a bypassed node, find the upstream source for a given output."""
        # Get nodes have no inputs; they cannot be bypassed through
        if self._is_get_node(node):
            return None
        inputs = node.get("inputs") or []
        for inp in inputs:
            inp_type = str(inp.get("type", ""))
            if inp_type == output_type and inp.get("link") is not None:
                link_id = int(inp["link"])
                link = self.links.get(link_id)
                if link:
                    return (link[1], link[2])
        if output_slot < len(inputs):
            inp = inputs[output_slot]
            if inp.get("link") is not None:
                link_id = int(inp["link"])
                link = self.links.get(link_id)
                if link:
                    return (link[1], link[2])
        for inp in inputs:
            if inp.get("link") is not None:
                link_id = int(inp["link"])
                link = self.links.get(link_id)
                if link:
                    return (link[1], link[2])
        return None

    # -- subgraph expansion --------------------------------------------------

    def _is_subgraph_instance(self, node: Dict[str, Any]) -> bool:
        """Check if a node is a subgraph instance."""
        node_type = str(node.get("type", ""))
        return node_type in self._subgraph_map

    def _expand_subgraph(
        self,
        instance_node: Dict[str, Any],
        instance_id: int,
        inherited_mode: int = 0,
        _depth: int = 0,
        _visited: Optional[Set[str]] = None,
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Tuple[str, int]]]]:
        """Expand a subgraph instance into internal nodes.

        Returns:
            - Dict of expanded_node_id_str → node_dict
            - Dict of output_slot_idx → list of (expanded_node_id_str, slot) for external wiring
        """
        if _visited is None:
            _visited = set()

        sg_id = str(instance_node.get("type", ""))
        sg = self._subgraph_map.get(sg_id)
        if sg is None:
            self.warnings.append(f"Subgraph definition {sg_id} not found for node {instance_id}.")
            return {}, {}

        sg_name = sg.get("name", sg_id)
        cycle_key = f"{sg_id}:{instance_id}"
        if cycle_key in _visited or _depth > 20:
            self.warnings.append(f"Subgraph cycle detected at '{sg_name}' (node {instance_id}); skipping.")
            return {}, {}

        _visited = _visited | {cycle_key}
        self._expanded_subgraph_count += 1

        # Determine effective mode for this instance
        instance_mode = self._effective_mode(instance_node, instance_id)
        if inherited_mode in (_MODE_BYPASS, _MODE_MUTE):
            instance_mode = inherited_mode
        # Check if disabled by subgraph name
        if sg_name in self._disabled_subgraph_names:
            instance_mode = _MODE_BYPASS

        # Build ID mapping: internal_id → expanded_id_str
        id_map: Dict[int, str] = {}
        for internal_node in sg.get("nodes") or []:
            internal_id = int(internal_node["id"])
            expanded_id = f"{instance_id}:{internal_id}"
            id_map[internal_id] = expanded_id

        # Get input/output mappings
        input_targets = _subgraph_input_targets(sg)
        output_sources = _subgraph_output_sources(sg)

        # Build internal links lookup
        internal_links: Dict[int, Dict[str, Any]] = {}
        for raw_link in sg.get("links") or []:
            if isinstance(raw_link, dict):
                internal_links[int(raw_link["id"])] = raw_link
            else:
                lid, onode, oslot, tnode, tslot, ltype = _parse_link(raw_link)
                internal_links[lid] = {
                    "id": lid, "origin_id": onode, "origin_slot": oslot,
                    "target_id": tnode, "target_slot": tslot, "type": ltype,
                }

        # Process instance inputs: map external links to internal targets
        # instance_node["inputs"] corresponds to subgraph inputs by index
        instance_inputs = instance_node.get("inputs") or []
        instance_widgets = instance_node.get("widgets_values") or []

        # For each subgraph input slot, find what the instance provides
        # slot_idx → (external_origin_id, external_origin_slot) or widget_value
        external_input_map: Dict[int, Any] = {}  # slot_idx → (origin_id, origin_slot) or ("WIDGET", value)

        # First pass: collect all linked inputs
        for slot_idx, inp_spec in enumerate(instance_inputs):
            if slot_idx >= len(sg.get("inputs") or []):
                break
            sg_input = sg["inputs"][slot_idx]
            link_id = inp_spec.get("link")
            if link_id is not None:
                link = self.links.get(int(link_id))
                if link:
                    external_input_map[slot_idx] = (link[1], link[2])  # (origin_node, origin_slot)

        # Second pass: assign widget values to ALL widget-type inputs in order
        # Widget values correspond to inputs that are "widget-like" (COMBO, INT, FLOAT, STRING, etc.)
        # regardless of whether they have an external link or not.
        # The widgets_values array contains values for all widget-type inputs in order.
        widget_idx = 0
        for slot_idx, sg_input in enumerate(sg.get("inputs") or []):
            input_type = str(sg_input.get("type", "")).upper()
            # Check if this is a widget-type input (INT, FLOAT, STRING, COMBO, BOOLEAN, NUMBER)
            is_widget_type = input_type in _WIDGET_TYPE_NAMES
            if not is_widget_type:
                continue
            if widget_idx < len(instance_widgets):
                # Only set widget value if no external link already set it
                if slot_idx not in external_input_map:
                    external_input_map[slot_idx] = ("WIDGET", instance_widgets[widget_idx])
                widget_idx += 1

        # Now expand internal nodes
        expanded_nodes: Dict[str, Dict[str, Any]] = {}
        expanded_outputs: Dict[str, List[Tuple[str, int]]] = {}  # slot_idx → [(node_id_str, slot)]

        for internal_node in sg.get("nodes") or []:
            internal_id = int(internal_node["id"])
            expanded_id = id_map[internal_id]

            # Check if this internal node is itself a subgraph instance (nested)
            if self._is_subgraph_instance(internal_node):
                nested_nodes, nested_outputs = self._expand_subgraph(
                    internal_node, internal_id,
                    inherited_mode=instance_mode,
                    _depth=_depth + 1,
                    _visited=_visited,
                )
                # Merge nested expanded nodes with prefix
                for nid, ndata in nested_nodes.items():
                    full_id = f"{instance_id}:{nid}"
                    expanded_nodes[full_id] = ndata
                # Remap nested outputs
                for slot_idx, sources in nested_outputs.items():
                    remapped = []
                    for src_id, src_slot in sources:
                        remapped.append((f"{instance_id}:{src_id}", src_slot))
                    expanded_outputs[f"nested_{internal_id}_{slot_idx}"] = remapped
                continue

            # Apply mode inheritance
            node_mode = int(internal_node.get("mode", 0))
            if instance_mode in (_MODE_BYPASS, _MODE_MUTE):
                node_mode = instance_mode

            if node_mode in (_MODE_BYPASS, _MODE_MUTE):
                self._subgraph_bypass_cut_count += 1
                # Don't include bypassed/muted nodes, but track for output resolution
                continue

            # Build the expanded node
            class_type = internal_node.get("type", "")

            # Check if class_type exists in object_info
            if not self._class_type_exists(class_type):
                self.warnings.append(
                    f"Subgraph '{sg_name}' node {internal_id}: unknown type '{class_type}'; skipped."
                )
                continue

            widget_names = self._widget_names(class_type)
            widget_values = list(internal_node.get("widgets_values") or [])

            inputs: Dict[str, Any] = {}

            # 1. Start with internal widget values
            linked_widget_names: set[str] = set()
            for inp in internal_node.get("inputs") or []:
                w = inp.get("widget")
                if w and isinstance(w, dict) and w.get("name"):
                    linked_widget_names.add(str(w["name"]))

            for idx, wname in enumerate(widget_names):
                if wname in linked_widget_names:
                    continue
                if idx < len(widget_values):
                    inputs[wname] = widget_values[idx]

            # 2. Process internal links ( remap IDs )
            for inp in internal_node.get("inputs") or []:
                if inp.get("link") is None:
                    continue
                link_id = int(inp["link"])
                link = internal_links.get(link_id)
                if not link:
                    continue

                origin_id = int(link["origin_id"])
                origin_slot = int(link["origin_slot"])
                target_slot = int(link["target_slot"])
                inp_name = str(inp.get("name", f"input_{target_slot}"))

                # Check if this link comes from the subgraph input stub
                if origin_id == _SUBGRAPH_INPUT_STUB:
                    # This input is fed by the subgraph's external interface
                    sg_slot_idx = origin_slot
                    if sg_slot_idx in external_input_map:
                        ext = external_input_map[sg_slot_idx]
                        if isinstance(ext, tuple) and len(ext) == 2 and ext[0] != "WIDGET":
                            # External link from main graph
                            inputs[inp_name] = [str(ext[0]), ext[1]]
                        elif isinstance(ext, tuple) and ext[0] == "WIDGET":
                            # Widget value override
                            inputs[inp_name] = ext[1]
                    continue

                # Normal internal link - remap IDs
                if origin_id in id_map:
                    inputs[inp_name] = [id_map[origin_id], origin_slot]
                else:
                    # Link to a bypassed node or missing node
                    pass

            expanded_nodes[expanded_id] = {
                "class_type": class_type,
                "inputs": inputs,
            }

        # Build output mapping: for each subgraph output slot, find the source
        for slot_idx, out_spec in enumerate(sg.get("outputs") or []):
            sources: List[Tuple[str, int]] = []
            for lid in out_spec.get("linkIds") or []:
                link = internal_links.get(int(lid))
                if link and int(link["target_id"]) == _SUBGRAPH_OUTPUT_STUB:
                    origin_id = int(link["origin_id"])
                    origin_slot = int(link["origin_slot"])
                    if origin_id in id_map:
                        sources.append((id_map[origin_id], origin_slot))
                    elif origin_id in self._subgraph_map:
                        # Output comes from a nested subgraph
                        nested_key = f"nested_{origin_id}_{origin_slot}"
                        if nested_key in expanded_outputs:
                            sources.extend(expanded_outputs[nested_key])
            expanded_outputs[str(slot_idx)] = sources

        return expanded_nodes, expanded_outputs

    # -- recursive resolution ------------------------------------------------

    def _resolve_node(self, node_id: int) -> Optional[Dict[str, Any]]:
        """Resolve a single node to its API-prompt dict (or None if skipped)."""
        key = str(node_id)
        if key in self._resolved:
            return self._resolved[key]
        if key in self._resolving:
            self.warnings.append(f"Cycle detected at node {node_id}; skipping.")
            return None

        node = self.nodes.get(node_id)
        if node is None:
            self.warnings.append(f"Node {node_id} referenced but not found in graph.")
            return None

        mode = self._effective_mode(node, node_id)

        if mode == _MODE_MUTE:
            self.warnings.append(
                f"Node {node_id} ({node.get('type','?')}) is muted; output unresolved."
            )
            self._resolved[key] = None
            return None

        if mode == _MODE_BYPASS:
            self._resolved[key] = None
            return None

        # Check if this is a subgraph instance
        if self._is_subgraph_instance(node):
            return self._resolve_subgraph_instance(node_id, node)

        # Handle Set/Get virtual nodes
        if self._is_set_node(node):
            # Set nodes are virtual - they don't appear in output
            # Their data source is resolved when a Get references them
            self._resolved[key] = None
            return None

        if self._is_get_node(node):
            # Get nodes are virtual - they don't appear in output
            # Their references are resolved to the Set's data source
            self._resolved[key] = None
            return None

        # Silently skip frontend UI nodes
        if self._is_frontend_ui(node):
            self._resolved[key] = None
            return None

        # Normal node
        self._resolving.add(key)
        try:
            class_type = node.get("type", "")

            # Check unknown type
            if not self._class_type_exists(class_type):
                self.warnings.append(
                    f"Node {node_id}: unknown type '{class_type}'; skipped."
                )
                self._resolved[key] = None
                return None

            widget_names = self._widget_names(class_type)
            widget_values = node.get("widgets_values") or []

            inputs: Dict[str, Any] = {}

            linked_widget_names: set[str] = set()
            for inp in node.get("inputs") or []:
                w = inp.get("widget")
                if w and isinstance(w, dict) and w.get("name"):
                    linked_widget_names.add(str(w["name"]))

            for idx, wname in enumerate(widget_names):
                if wname in linked_widget_names:
                    continue
                if idx < len(widget_values):
                    inputs[wname] = widget_values[idx]

            for inp in node.get("inputs") or []:
                if inp.get("link") is None:
                    continue
                link_id = int(inp["link"])
                link = self.links.get(link_id)
                if not link:
                    self.warnings.append(
                        f"Node {node_id}: link {link_id} not found in graph."
                    )
                    continue
                origin_node, origin_slot = link[1], link[2]
                inp_name = str(inp.get("name", f"input_{link[4]}"))
                inputs[inp_name] = [str(origin_node), origin_slot]

            result = {"class_type": class_type, "inputs": inputs}
            self._resolved[key] = result
            return result
        finally:
            self._resolving.discard(key)

    def _resolve_subgraph_instance(
        self, node_id: int, node: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Expand a subgraph instance and return a placeholder result.

        The actual expanded nodes are stored separately and merged in convert().
        """
        key = str(node_id)
        if key in self._resolved:
            return self._resolved[key]

        expanded_nodes, expanded_outputs = self._expand_subgraph(node, node_id)

        if not expanded_nodes:
            # Subgraph was fully bypassed/muted or empty
            self._resolved[key] = None
            return None

        # Store expanded nodes for later merging
        if not hasattr(self, '_pending_expanded'):
            self._pending_expanded: Dict[str, Dict[str, Any]] = {}
        if not hasattr(self, '_pending_outputs'):
            self._pending_outputs: Dict[int, Dict[str, List[Tuple[str, int]]]] = {}

        self._pending_expanded.update(expanded_nodes)
        self._pending_outputs[node_id] = expanded_outputs

        # The instance itself becomes a virtual node - we don't add it to prompt
        # Instead, downstream nodes will reference the expanded internal nodes directly
        self._resolved[key] = None  # Mark as processed

        return None

    def _resolve_output(
        self, node_id: int, output_slot: int, output_type: str, _depth: int = 0
    ) -> Optional[Tuple[str, int]]:
        """Resolve the origin (node_id_str, slot) that feeds a given output.

        Follows bypass chains and subgraph boundaries.
        """
        if _depth > 100:
            self.warnings.append(
                f"Bypass chain too deep at node {node_id} output {output_slot}."
            )
            return None

        node = self.nodes.get(node_id)
        if node is None:
            return None

        mode = self._effective_mode(node, node_id)

        if mode == _MODE_MUTE:
            self.warnings.append(
                f"Node {node_id} ({node.get('type','?')}) is muted; output {output_slot} unresolved."
            )
            return None

        if mode == _MODE_BYPASS:
            # Get nodes have no inputs; bypassing them means their references
            # become unresolvable. Silently return None (no warning).
            if self._is_get_node(node):
                return None
            src = self._find_bypass_source(node, output_slot, output_type)
            if src is None:
                self.warnings.append(
                    f"Bypass node {node_id} ({node.get('type','?')}): "
                    f"no same-type input found for output {output_slot} ({output_type})."
                )
                return None
            return self._resolve_output(src[0], src[1], output_type, _depth + 1)

        # Check if this is a subgraph instance
        if self._is_subgraph_instance(node):
            # Ensure the subgraph is expanded
            self._resolve_node(node_id)
            # Look up the output mapping
            if hasattr(self, '_pending_outputs') and node_id in self._pending_outputs:
                outputs = self._pending_outputs[node_id]
                slot_key = str(output_slot)
                if slot_key in outputs and outputs[slot_key]:
                    return outputs[slot_key][0]  # Return first source
            return None

        # Check if this is a Set node - resolve through to its data source
        if self._is_set_node(node):
            # Find the Set's input link
            inputs = node.get("inputs") or []
            for inp in inputs:
                if inp.get("link") is not None:
                    link_id = int(inp["link"])
                    link = self.links.get(link_id)
                    if link:
                        return self._resolve_output(link[1], link[2], output_type, _depth + 1)
            return None

        # Check if this is a Get node - resolve through to the Set's data source
        if self._is_get_node(node):
            src = self._find_set_source(node_id)
            if src is None:
                return None
            if src[1] == -1:
                # Constant Set with no input link - cannot resolve as a link
                return None
            return self._resolve_output(src[0], src[1], output_type, _depth + 1)

        # Normal node
        return (str(node_id), output_slot)

    # -- public API ----------------------------------------------------------

    def convert(self) -> Dict[str, Any]:
        prompt: Dict[str, Any] = {}

        # Initialize pending expanded storage
        self._pending_expanded: Dict[str, Dict[str, Any]] = {}
        self._pending_outputs: Dict[int, Dict[str, List[Tuple[str, int]]]] = {}

        # Phase 1: Resolve all top-level nodes (this triggers subgraph expansion)
        for node_id, node in self.nodes.items():
            mode = self._effective_mode(node, node_id)
            if mode in (_MODE_BYPASS, _MODE_MUTE):
                continue
            self._resolve_node(node_id)

        # Phase 2: Merge expanded subgraph nodes into prompt
        for expanded_id, node_data in self._pending_expanded.items():
            prompt[expanded_id] = node_data

        # Phase 3: Resolve linked inputs through bypass chains and subgraph boundaries
        for node_id, node in self.nodes.items():
            mode = self._effective_mode(node, node_id)
            if mode in (_MODE_BYPASS, _MODE_MUTE):
                continue

            key = str(node_id)
            resolved = self._resolved.get(key)
            if resolved is None:
                continue

            # This is a normal node - resolve its inputs
            inputs = resolved.get("inputs", {})
            for inp_name, val in list(inputs.items()):
                if isinstance(val, list) and len(val) == 2:
                    origin_ref = val[0]
                    origin_slot = int(val[1])

                    # Check if origin is a subgraph instance (integer ID)
                    try:
                        origin_node_id = int(origin_ref)
                    except (ValueError, TypeError):
                        origin_node_id = None

                    if origin_node_id is not None and origin_node_id in self.nodes:
                        # Origin is a main graph node
                        inp_type = ""
                        for inp in node.get("inputs") or []:
                            if str(inp.get("name", "")) == inp_name:
                                inp_type = str(inp.get("type", ""))
                                break

                        final = self._resolve_output(origin_node_id, origin_slot, inp_type)
                        if final is None:
                            del inputs[inp_name]
                        else:
                            inputs[inp_name] = [str(final[0]), final[1]]
                    # else: origin is already an expanded node ID (string like "807:123"), keep as-is

            prompt[key] = resolved

        # Phase 4: Resolve inputs of expanded subgraph nodes
        for expanded_id, node_data in list(prompt.items()):
            if ":" not in expanded_id:
                continue  # already processed above
            inputs = node_data.get("inputs", {})
            for inp_name, val in list(inputs.items()):
                if isinstance(val, list) and len(val) == 2:
                    origin_ref = str(val[0])
                    origin_slot = int(val[1])

                    # Check if origin is a main graph node (needs resolution)
                    try:
                        origin_node_id = int(origin_ref)
                    except (ValueError, TypeError):
                        origin_node_id = None

                    if origin_node_id is not None and origin_node_id in self.nodes:
                        # Origin is in main graph - resolve through bypass/subgraph
                        origin_node = self.nodes[origin_node_id]
                        inp_type = ""
                        for inp in origin_node.get("inputs") or []:
                            if str(inp.get("name", "")) == inp_name:
                                inp_type = str(inp.get("type", ""))
                                break

                        final = self._resolve_output(origin_node_id, origin_slot, inp_type)
                        if final is None:
                            del inputs[inp_name]
                        else:
                            inputs[inp_name] = [str(final[0]), final[1]]
                    # else: origin is already an expanded node ID, keep as-is

        # Phase 5: Resolve Set/Get references
        # For each Get node, find all downstream references and replace them
        self._resolve_setget_references(prompt)

        return {"prompt": prompt, "warnings": self.warnings}

    def _resolve_setget_references(self, prompt: Dict[str, Any]) -> None:
        """Replace all references to Get nodes with references to their Set's data source.

        After this phase, no Get node IDs should remain as link origins in the prompt.
        """
        # Build a mapping: get_node_id → resolved source (node_id_str, slot)
        get_resolutions: Dict[int, Tuple[str, int]] = {}

        for get_id in self._get_nodes:
            src = self._find_set_source(get_id)
            if src is None:
                continue
            set_id, set_slot = src
            if set_slot == -1:
                # Constant Set with no input link - cannot resolve as a link
                self.warnings.append(
                    f"Get node {get_id}: Set node {set_id} has no input link (constant value); cannot resolve reference."
                )
                continue
            # Resolve the Set's source through bypass/subgraph chains
            set_node = self.nodes.get(set_id)
            if set_node is None:
                continue
            # Find the input type from the Set node
            inp_type = ""
            for inp in set_node.get("inputs") or []:
                if inp.get("link") is not None:
                    inp_type = str(inp.get("type", ""))
                    break

            resolved = self._resolve_output(set_id, set_slot, inp_type)
            if resolved is not None:
                get_resolutions[get_id] = resolved

        # Now replace all references to Get nodes in the prompt
        for node_id, node_data in prompt.items():
            inputs = node_data.get("inputs", {})
            for inp_name, val in list(inputs.items()):
                if isinstance(val, list) and len(val) == 2:
                    origin_ref = str(val[0])
                    origin_slot = int(val[1])
                    try:
                        origin_int = int(origin_ref)
                    except (ValueError, TypeError):
                        continue
                    if origin_int in get_resolutions:
                        new_src, new_slot = get_resolutions[origin_int]
                        inputs[inp_name] = [str(new_src), new_slot]

    # -- stats for testing ---------------------------------------------------

    def get_stats(self) -> Dict[str, int]:
        """Return expansion statistics."""
        return {
            "expanded_subgraph_count": self._expanded_subgraph_count,
            "subgraph_bypass_cut_count": self._subgraph_bypass_cut_count,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def graph_to_prompt(
    graph: Dict[str, Any],
    *,
    object_info: Optional[Dict[str, Any]] = None,
    object_info_url: str = "http://127.0.0.1:8188/object_info",
    disabled_groups: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Convert a ComfyUI graph (canvas format) to API prompt format.

    Parameters
    ----------
    graph:
        The graph JSON dict (keys: ``nodes``, ``links``, ``groups``,
        optionally ``definitions.subgraphs``).
    object_info:
        Optional pre-fetched ``/object_info`` dict.  If omitted, it will be
        fetched from *object_info_url*.
    object_info_url:
        URL of the running ComfyUI instance's ``/object_info`` endpoint.
    disabled_groups:
        List of group titles or subgraph names whose nodes should be treated
        as bypassed.

    Returns
    -------
    dict with keys ``prompt`` (the API-format prompt) and ``warnings``.
    """
    converter = _GraphToPrompt(
        graph,
        object_info=object_info,
        object_info_url=object_info_url,
        disabled_groups=disabled_groups,
    )
    return converter.convert()
