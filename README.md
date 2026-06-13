# ComfyUI Studio Suite

English | [简体中文](README.zh-CN.md)

ComfyUI Studio Suite is a preview custom-node bundle for ComfyUI focused on prompt editing, LLM-assisted anime tag workflows, local caption generation, workflow queue helpers, and image prep utilities.

Current release status: `v0.1-preview`

This repository is intended for early testing and integration. The core workflows are usable, but some parts are still being hardened for a broader public release.

## What It Does

- Prompt and tag generation for anime / Danbooru-style workflows
- Chinese-to-English tag translation, tag expansion, and model-specific normalization
- WD14 + LLM-assisted caption generation for training datasets
- Local GGUF execution through `llama_cpp_python_inproc`
- Optional backend adapters for KoboldCpp, llama.cpp server, LM Studio, vLLM, and OpenAI-compatible APIs
- Folder-based queue helpers for image/text batch workflows
- Fill / crop / resize image helper nodes

## Recommended Current Setup

The most tested path in this preview is:

- Text tasks: `Gemma 4 E4B GGUF Q4`
- Vision tasks: `Gemma 4 E4B Vision GGUF` plus matching `mmproj`
- Backend provider: `llama_cpp_python_inproc`

Qwen-based paths may work, but the Gemma 4 E4B route is currently the safest recommendation.

## Installation

1. Put this folder under:

```text
ComfyUI/custom_nodes/comfyui_studio_suite
```

2. Restart ComfyUI.

3. Create your local backend profile file:

```text
config/backend_profiles.example.json -> config/backend_profiles.json
```

4. Edit `config/backend_profiles.json` and set your local `model_path` and `mmproj_path`.

5. Open one of the example workflows from:

```text
examples/workflows
```

6. Run the release/install self-check from the node root:

```powershell
python scripts/doctor_release.py
```

## Quick Start

### 1. First text-only smoke test

Use:

- `examples/workflows/noob_zh_to_en_expand_preview.json`

This workflow is the recommended first-run validation for:

- task bundle composition
- resource loading
- local text LLM execution
- NoobAI-oriented prompt formatting

### 2. First vision caption test

Use:

- `examples/workflows/tagging_wd14_llm_anima_train_preview.json`

This workflow combines:

- image queue input
- WD14 base tags
- image-path bridging
- vision-capable local LLM refinement
- Anima-style training caption formatting

## Example Workflows

- `examples/workflows/noob_zh_to_en_expand_preview.json`
  - Chinese description -> English Danbooru-style prompt
  - NoobAI XL 1.1 oriented formatting
  - text-only

- `examples/workflows/tagging_wd14_llm_anima_train_preview.json`
  - WD14 tags + local vision LLM caption refinement
  - intended for training-caption workflows
  - requires a vision-capable GGUF and `mmproj`

Detailed notes are in:

- `examples/workflows/README.md`

## Resource Policy

This repository keeps Git-tracked resources lightweight by default.

Bundled resources include:

- `resources/danbooru_character_aliases.json`
- `resources/character_alias_safety.json`
- `resources/task_templates`
- `resources/task_bundles`
- lightweight clothing helper resources

Large optional resources should be distributed separately, for example through Hugging Face Datasets or release assets:

- generated character alias dictionaries
- large Danbooru character tables
- tag-count statistics
- tag co-occurrence CSV files
- artist wildcard lists

Primary dataset link:

- `https://huggingface.co/datasets/onglon114514/ComfyUI-Studio-Suite-Resources`

See:

- `resources/README.md`
- `docs/HUGGINGFACE_RESOURCES.md`

## Backend Modes

Supported execution directions:

- `llama_cpp_python_inproc`
  - runs GGUF inference inside the ComfyUI Python process
  - current recommended default

- `koboldcpp`
  - managed local backend

- `llama_cpp_server`
  - managed llama.cpp server backend

- `lm_studio`
- `vllm`
- `custom_openai_compat`
  - attach to an existing OpenAI-compatible endpoint

## Repository Layout

- `task_agent_core/`
  - Task Agent nodes and execution-side logic
- `task_agent_gateway.py`
  - backend adapter and task runtime core
- `queue_nodes.py`
  - independent prompt queue helpers
- `smart_fill_crop_resize_node.py`
  - image fill/crop/resize helper
- `frontend/`, `web/`
  - frontend assets
- `examples/workflows/`
  - tested preview workflows
- `resources/`
  - bundled lightweight resources and optional large-resource slots
- `docs/`
  - setup, backend, migration, and release notes
- `scripts/`
  - packaging, diagnostics, and helper scripts

## Known Scope

This preview is best described as:

- stable enough for internal use and early external testing
- not yet a finished “install once and forget” public release

The main remaining friction points are:

- model-path setup still requires manual editing
- large optional resources are intentionally not bundled
- some Prompt Studio and extended workflows are still under active refinement

## Attribution

This repository integrates and rebuilds functionality from several development lines. See:

- `docs/ATTRIBUTION.md`

## Release Preparation

Before packaging or publishing updates:

```powershell
python scripts/doctor_release.py
python scripts/build_release_preview.py
```

This checks for:

- missing required files
- local-path leakage
- large-resource packaging mistakes
- release structure problems
