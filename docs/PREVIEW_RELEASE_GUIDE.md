# Preview Release Guide

This guide is for `v0.1-preview` style sharing.

The preview goal is practical internal testing, not a polished public release.

## Recommended package path

Copy the release folder into:

```text
ComfyUI/custom_nodes/comfyui_studio_suite
```

Then restart ComfyUI.

## First check

Run:

```powershell
python scripts/doctor_release.py
```

If using ComfyUI portable Python:

```powershell
path\to\ComfyUI\python\python.exe scripts\doctor_release.py
```

The script checks:

- required node files
- JSON config validity
- task templates and task bundles
- bundled resource presence
- private `llama-cpp-python` runtime
- local absolute paths that must be edited for a new machine

Warnings about missing model files are expected until the user edits `config/backend_profiles.json`.

## Config files

Use these files:

- `config/task_agent_config.local.json`
  - created from `task_agent_config.example.json` in release packages
  - controls backend provider defaults and llama.cpp runtime options
- `config/backend_profiles.json`
  - created from `backend_profiles.example.json` in release packages
  - users must edit model paths here

Do not publish your local `task_agent_config.local.json` or local `backend_profiles.json` if they contain personal absolute paths.

## Recommended model target

For preview users, recommend only:

- `Gemma 4 E4B Q4`
  - best default for text tag tools
  - tested with `llama_cpp_python_inproc`
- `Gemma 4 E4B vision + mmproj`
  - experimental visual tagging only
  - JSON output may need fallback cleanup

Do not recommend Qwen profiles as the default preview target yet.

## Runtime modes

Recommended order:

1. `llama_cpp_python_inproc`
   - simplest user experience
   - no external backend window
   - good enough for short text tag tasks
2. `koboldcpp`
   - still useful when the user already has it
   - better for heavier models or familiar setups
3. `llama_cpp_server`, `LM Studio`, `vLLM`
   - compatibility routes
   - require more user setup

## Large resources

Small resources should stay in the node package:

- `danbooru_character_aliases.json`
- `character_alias_safety.json`
- task templates
- task bundles
- prompt profiles

Large resources may be shipped separately:

- `danbooru_character_aliases.generated.json`
- `danbooru_character_webui.normalized.jsonl`
- `danbooru_tags_cooccurrence.csv`
- `tag_count_tags_统计.jsonl`
- `danbooru_artist_wildcard-D站画师列表.txt`

Use the package script without large resources for a smaller preview:

```powershell
python scripts/build_release_preview.py
```

Use this only when sharing the full resource pack:

```powershell
python scripts/build_release_preview.py --include-large-resources
```

## Known limitations

- Visual in-process support is experimental.
- The prompt editor frontend has been repaired but should still be tested in a clean ComfyUI install.
- Some docs still contain local development paths for handoff/history context.
- `backend_profiles.json` in the development folder may contain local model paths; release packages should use `backend_profiles.example.json`.
- `runtime/*.kcpps` files are generated runtime artifacts and should not be published as default config.

## Minimal preview workflows to provide

Prepare these before sharing broadly:

- text tag utility:
  - `Prompt Studio` or text input -> `Task Agent Tag Utility`
  - `chain_zh_to_anima_prompt` or `chain_zh_to_noobai_prompt`
- WD14 assisted caption:
  - WD14 tags -> `chain_wd14_to_anima_train_caption`
- visual smoke test:
  - image path bridge -> visual profile -> `extract_tags_from_image`
  - mark as experimental
