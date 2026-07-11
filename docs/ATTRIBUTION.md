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

## Retained And Adapted MIT Frontend

- [`weilin9999/WeiLin-ComfyUI-prompt-all-in-one`](https://github.com/weilin9999/WeiLin-ComfyUI-prompt-all-in-one)
  - Original author: WeiLin, copyright 2024, MIT License.
  - Prompt Studio retains and adapts the editor bundle, static UI assets, tag interaction data, and i18n data.
  - Studio Suite replaces the original backend, translation/network path, ComfyUI node integration, storage handling, LoRA metadata adapter, and local LLM workflow.
  - The required copyright and MIT notice are included in `THIRD_PARTY_NOTICES.md`.

## Release Hygiene Still Required

Before a fully public stable release:

1. Confirm the license compatibility of every other directly merged module.
2. Document which remaining files are direct modifications and which are rewrites.
3. Keep internal source paths, private model paths, API keys, and local runtime artifacts out of Git.
