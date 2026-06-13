# ComfyUI Studio Suite

ComfyUI Studio Suite is a preview ComfyUI custom node package for prompt editing, LLM-assisted Danbooru tag workflows, image captioning helpers, independent prompt queues, and image fill/crop/resize utilities.

Current status: `v0.1-preview`

This is usable for early testing, but it is not yet a polished public release. Expect config files and model paths to be edited manually.

## Main Features

- Task Agent nodes for tag translation, expansion, normalization, and caption generation.
- Modular context composition with task bundles, resource bundles, system prompts, character cards, world-book style notes, and regex rules.
- Direct local LLM execution through `llama_cpp_python_inproc`.
- Optional backend adapters for KoboldCpp, llama.cpp server, LM Studio, vLLM, and OpenAI-compatible endpoints.
- Prompt Studio editor frontend for prompt writing and prompt text output.
- Independent prompt queue nodes for folder-based batch workflows.
- Smart fill/crop/resize helper node.

## Recommended First Use

For the current preview, the most tested path is:

- Text-only prompt/tag tasks: Gemma 4 E4B GGUF Q4.
- Vision-assisted caption tasks: Gemma 4 E4B vision GGUF plus its matching `mmproj`.
- Backend provider: `llama_cpp_python_inproc`.

Qwen and larger models may work, but they have not been polished to the same level as the Gemma 4 E4B test path.

## Installation

1. Copy this folder into:

```text
ComfyUI/custom_nodes/comfyui_studio_suite
```

2. Restart ComfyUI.

3. Copy the example backend profile:

```text
config/backend_profiles.example.json -> config/backend_profiles.json
```

4. Edit `config/backend_profiles.json` and point `model_path` / `mmproj_path` to your local GGUF files.

5. Open one of the example workflows in:

```text
examples/workflows
```

6. Run:

```powershell
python scripts/doctor_release.py
```

The doctor script checks required files, common path mistakes, and release packaging risks.

## Example Workflows

- `examples/workflows/noob_zh_to_en_expand_preview.json`
  - Chinese text to NoobAI XL 1.1 style English Danbooru prompt.
  - Text-only.
  - Best first smoke test.

- `examples/workflows/tagging_wd14_llm_anima_train_preview.json`
  - WD14 tags plus vision LLM natural-language caption for Anima-style training captions.
  - Requires a vision model and `mmproj`.
  - Intended for dataset captioning workflows.

See `examples/workflows/README.md` for detailed notes.

## Resources

The GitHub repository should only include lightweight resources by default:

- `resources/danbooru_character_aliases.json`
- `resources/character_alias_safety.json`
- `resources/task_templates`
- `resources/task_bundles`
- clothing helper resources, if their license allows redistribution

Large optional resources should be hosted separately, for example on Hugging Face Datasets or release assets:

- generated Danbooru character alias dictionary
- Danbooru character JSONL / XLSX source tables
- tag count statistics
- tag co-occurrence CSV
- artist wildcard lists

After downloading large resources, place them under `resources/` with the filenames referenced by `resources/README.md`.

## Backend Modes

Supported directions:

- `llama_cpp_python_inproc`
  - Runs GGUF inference inside the ComfyUI Python process.
  - Best for avoiding a separate KoboldCpp window.

- `koboldcpp`
  - Managed local backend.
  - Useful if you already prefer KoboldCpp performance and behavior.

- `llama_cpp_server`
  - Managed llama.cpp server backend.

- `lm_studio`, `vllm`, `custom_openai_compat`
  - Attach to an already-running OpenAI-compatible endpoint.

## Project Layout

- `__init__.py`
  - ComfyUI node package entry.
- `task_agent_core/`
  - Task Agent node definitions.
- `task_agent_gateway.py`
  - Backend adapter and task execution core.
- `queue_nodes.py`
  - Independent prompt queue helpers.
- `smart_fill_crop_resize_node.py`
  - Image prep helper.
- `frontend/`, `web/`
  - Prompt Studio and frontend assets.
- `resources/`
  - Lightweight bundled resources and optional large-resource slots.
- `examples/workflows/`
  - Example workflows.
- `docs/`
  - Setup, backend, and release documentation.
- `scripts/`
  - Packaging, diagnostics, and setup helpers.

## Release Notes

This preview intentionally keeps task definitions modular. Users can add their own JSON task bundles, prompt templates, resource files, and backend profiles without modifying the node core.

Before publishing a release, run:

```powershell
python scripts/doctor_release.py
python scripts/build_release_preview.py
```

Then check that the preview package does not include local model paths, private keys, runtime cache files, or large resources that should be hosted separately.
