# Local Backend Setup

This package now supports direct task execution inside ComfyUI. For normal local use, you do not need to launch a separate task gateway window first.

## Recommended beginner layout

1. Put one managed backend runtime under:
   - `koboldcpp`:
     - `runtime/koboldcpp`
   - `llama.cpp server`:
     - `runtime/llama.cpp`
2. In ComfyUI Task Agent nodes:
   - keep `gateway_url` as `http://127.0.0.1:8765`
   - choose `backend_provider`
   - turn on `auto_load_backend`
   - choose `model_source = custom_path` if you do not want to edit preset JSON files
   - fill `custom_model_path`
   - fill `custom_mmproj_path` only for vision models

Quick setup helper:

- `scripts/setup_managed_backend.ps1`

## Path resolution behavior

The package now resolves backend files in this order:

- explicit absolute path from config
- project-relative path such as `config/gemma4_e4b_managed.kcpps`
- bundled local runtime path such as `runtime/koboldcpp/koboldcpp.exe`
- bundled local runtime path such as `runtime/llama.cpp/llama-server.exe`
- old project-relative paths, remapped into the current package root when possible

## Existing backend / API mode

If you already have an OpenAI-compatible backend running elsewhere:

- put its real URL into `gateway_url`
- choose an attach-existing provider such as `lm_studio`, `vllm`, or `custom_openai_compat`
- the node will treat it as an attached backend instead of launching a local managed runtime

## Preset profiles vs custom path

- `profile_catalog`: use preset model paths from `config/backend_profiles.json`
- `custom_path`: safest for moving the package between machines, because the workflow can point directly at the local model file

## Current practical default

For portable local deployment, the lowest-friction route is one of these:

- bundle `koboldcpp.exe` inside the node package
- or bundle `llama-server.exe` and companion files inside the node package
- keep model files outside the package
- select `custom_path` in the node
- let the node autoload and unload the backend per task

Related docs:

- `docs/STANDALONE_RUNTIME_SETUP.md`
- `docs/BACKEND_SWITCH_GUIDE.md`
