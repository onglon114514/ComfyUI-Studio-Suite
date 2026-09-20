# AGENTS.md

## Project

This repository is ComfyUI Studio Suite, a ComfyUI custom-node package. The authoritative handoff document is `docs/OPENCLAW_TRANSITION_zh-CN.md`; read it before making changes.

## Working Language And Scope

- Communicate with the user in Chinese.
- The source of truth is this repository, currently branch `codex/hy-mt-experimental`.
- The local ComfyUI deployment lives under `<ComfyUI>/custom_nodes/comfyui_studio_suite_prompt_studio_merge`; it contains machine-local config, runtime binaries, models, and user storage. Never replace or delete it wholesale. (The exact absolute path for the current machine is kept in the local, gitignored handoff notes.)
- Do not modify unrelated projects or experiment artifacts outside this repository.

## Safety

- The working tree contains user and in-progress changes. Never run `git reset --hard`, `git clean`, or destructive cleanup without explicit approval.
- Preserve `prompt_studio/storage/group_tags/`; it contains user-customized tags.
- Do not commit local config, model files, runtime binaries, generated dictionary databases, dumps, outputs, credentials, or large optional resources.
- Ask before installing dependencies, launching long GPU/batch jobs, pushing, or publishing.
- Keep machine-specific absolute paths out of committed files. Use placeholders such as `<ComfyUI>`, `<ComfyUI python>` or `<models dir>` instead; real paths belong in gitignored local files.
- Prefer small, reproducible fixes and actual verification over asking the user to repeatedly restart ComfyUI.

## Verification

Use the ComfyUI Python interpreter (substitute your own machine's paths):

```powershell
$py = "<ComfyUI python>\python.exe"
& $py -B scripts/doctor_release.py
$env:COMFYUI_ROOT = "<ComfyUI>"
& $py -B scripts/check_node_registry.py
```

Run the tests relevant to the files changed; see `docs/OPENCLAW_TRANSITION_zh-CN.md` for the full matrix.

## Reporting

结束时简要说明：修改文件、验证命令和结果、未覆盖风险，以及是否已同步到 ComfyUI 实装目录。

