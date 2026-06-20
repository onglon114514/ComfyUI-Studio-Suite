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
- XY matrix testing for LoRA files, LoRA strength, sampler/scheduler, FreeU, and generic node inputs
- Prompt Studio editor with bundled autocomplete / group-tag storage
- Fill / crop / resize image helper nodes

## Recommended Current Setup

The most tested path in this preview is:

- Text tasks: `Gemma 4 E4B GGUF Q4`
- Backend provider: `llama_cpp_python_inproc`
- Low-VRAM training caption baseline: WD14 original tags + Gemma 4 E4B Q4 text-only natural-language captioning
- Optional vision tasks: `Gemma 4 E4B Vision GGUF` plus matching `mmproj`

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

### 2. Stable low-VRAM tagging baseline

Use:

- `examples/workflows/tagging_wd14_llm_anima_train_preview.json`

Recommended conservative setup:

- WD14 produces the original tag line.
- Task Agent keeps the WD14 tag line unchanged.
- `Gemma 4 E4B Q4` adds only the natural-language caption line.
- Use `llama_cpp_python_inproc`, context `4096`, GPU layers `0`, batch `128`.
- Keep the LLM loaded during a queue batch, then unload with the cleanup node at the end.

Expected training text:

```text
wd14, original, tags, kept, as, first, line

Natural-language caption generated from the WD14 tags.
```

See:

- `docs/TAGGING_STABLE_BASELINE.md`

### 3. Optional vision caption test

The same workflow can be extended with:

- image-path bridging
- vision-capable local LLM refinement
- Anima-style training caption formatting

This requires a vision-capable GGUF and matching `mmproj`, and is not the low-VRAM baseline.

## Example Workflows

- `examples/workflows/noob_zh_to_en_expand_preview.json`
  - Chinese description -> English Danbooru-style prompt
  - NoobAI XL 1.1 oriented formatting
  - text-only

- `examples/workflows/tagging_wd14_llm_anima_train_preview.json`
  - WD14 tags + local LLM natural-language caption assistance
  - intended for training-caption workflows
  - low-VRAM baseline uses text-only Gemma 4 E4B Q4; vision refinement is optional

Detailed notes are in:

- `examples/workflows/README.md`

## XY / LoRA Testing

The current development line includes a generic XY matrix system:

- `Studio Suite XY Axis - LoRA File`
- `Studio Suite XY Axis - LoRA Strength`
- `Studio Suite XY Axis - Sampler/Scheduler`
- `Studio Suite XY Axis - FreeU`
- `Studio Suite XY Axis - Generic`
- `Studio Suite XY Matrix`
- `Studio Suite XY Queue`
- `Studio Suite XY Grid Builder`

For LoRA checkpoint comparison, place a target bridge after the LoRA loader and connect `target_ref` to the LoRA axis nodes. The queue submits one independent child prompt per cell, then the grid builder can create a contact sheet after the child images finish.

See:

- `docs/xy_matrix_nodes.zh-CN.md`

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

## Downloading Large Resources

If you only want to test the node core, the bundled lightweight resources are enough.

You should download the large optional resource bundle when you need:

- broader Danbooru character alias coverage
- large character-table lookups
- tag-count or co-occurrence assisted workflows
- artist wildcard reference resources
- expanded clothing lookup resources

Download source:

- `https://huggingface.co/datasets/onglon114514/ComfyUI-Studio-Suite-Resources`

After downloading, place the files into:

```text
ComfyUI/custom_nodes/comfyui_studio_suite/resources
```

Keep the original filenames unchanged.

Typical examples:

- `danbooru_character_aliases.generated.json`
- `danbooru_character_webui.normalized.jsonl`
- `danbooru_tags_cooccurrence.csv`
- `danbooru_artist_wildcard-D站画师列表.txt`
- `tag_count_tags_统计.jsonl`

The built-in resource loader will detect them automatically if the filenames match.

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
- `prompt_studio/storage/`
  - bundled Prompt Studio autocomplete, group tags, local complete tags, and prompt data
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
- Task Agent has a tested low-VRAM text-only tagging baseline, but long unattended production batches still need more testing
- managed KoboldCpp auto-launch is optional and not the default recommendation yet
- LoRA stack testing and LoRA block/layer weight testing are not implemented yet

See:

- `CHANGELOG.md`
- `docs/NEXT_RELEASE_TODO.md`

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
