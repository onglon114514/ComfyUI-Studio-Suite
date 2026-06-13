# Attribution

This package is an in-progress integrated ComfyUI node suite assembled from several development lines.

## Directly Merged Internal Modules

- `ComfyUI_IndependentPromptQueue`
  - Integrated as `queue_nodes.py`.
  - Provides folder queueing, image/text pairing, and result-writing helpers.

- `ComfyUI_SmartFillCropResize`
  - Integrated as `smart_fill_crop_resize_node.py` and related frontend assets.
  - Provides image fill/crop/resize helper functionality.

- `task_agent_core`
  - Integrated as `task_agent_core/`, `task_agent_gateway.py`, `config/`, and `resources/`.
  - Provides local LLM task execution, task bundles, resource loading, and backend adapters.

## Reference-Only Source

- `weilin-comfyui-prompt-all-in-one-page-unlock`
  - Used as a reference for prompt editor UX, tag editing interaction, autocomplete, and LoRA helper behavior.
  - Not intended to remain as a direct structural copy.
  - The Prompt Studio subsystem should continue moving toward a distinct implementation.

## Release Hygiene Still Required

Before a fully public stable release:

1. Add original repository URLs for any external source that remains relevant.
2. Confirm the license compatibility of every directly merged or referenced module.
3. Document which files are direct modifications and which are rewrites.
4. Keep internal source paths, private model paths, API keys, and local runtime artifacts out of Git.
