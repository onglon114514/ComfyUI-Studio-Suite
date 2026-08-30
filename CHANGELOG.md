# Changelog

## Next preview

This is the local development line intended for the next GitHub push after `v0.1-preview`.

### Added

- Added Studio Suite XY test nodes:
  - generic input axis
  - sampler/scheduler axis
  - FreeU preset axis
  - LoRA file axis
  - LoRA strength axis
  - XY matrix builder
  - independent XY queue
  - XY grid/contact-sheet builder
- Added XY target bridge nodes:
  - `MODEL + CLIP` bridge for standard `LoraLoader`
  - `MODEL` bridge for model-only LoRA loaders
- Added LoRA checkpoint comparison workflow support:
  - scan LoRA names from ComfyUI `loras`
  - filter by substring or wildcard
  - combine LoRA file axis with strength axis
  - write manifest JSON for later grid generation
- Added LoRA stack and block-weight XY helpers:
  - Efficiency/TSC LoRA Stacker axis
  - serial core `LoraLoader` chain axis
  - Inspire LoRA block-weight axis for layer/block tests
- Added Task Agent UI helpers:
  - dynamic slot refresh for task/resource/context module nodes
  - floating backend/task monitor panel
- Added a documented conservative tagging baseline for 4060-class machines:
  - WD14 tags are preserved as the first line
  - Gemma 4 E4B Q4 in-process text captioning adds the natural-language line
  - no vision LLM input is required for the baseline
- Added Prompt Studio bundled storage assets:
  - autocomplete word list
  - group tag YAML files
  - local complete tag CSV
  - prompt history/favorite/settings JSON copied from the local legacy Prompt Studio data
- Added missing Prompt Studio frontend modules required by the output-node editor buttons.
- Added documentation for XY matrix usage, LoRA comparison, and the stable tagging baseline.

### Changed

- Prompt Studio now pauses the ComfyUI canvas while its full-screen editor is open and destroys the iframe on close, preventing the hidden editor and graph renderer from accumulating work together.
- Prompt Studio accepts legacy editor messages only from its own same-origin iframe and caps prompt payload size before updating ComfyUI widgets.
- NewBie XML text editing now respects IME composition and synchronizes with the legacy PromptUI bridge on composition end or focus loss instead of dispatching a full update on every keystroke.
- The Task Agent monitor avoids unchanged DOM writes and uses a slower open-panel polling interval to reduce browser rendering pressure.
- Existing XY nodes now include Chinese field tooltips covering target connections, input names, line formats, queue settings, and grid output options.
- Prompt Studio now prefers `prompt_studio/storage` inside this package for autocomplete, group tags, local tag CSVs, and prompt data.
- Legacy disabled `weilin-comfyui-prompt-all-in-one-page-unlock` data is now only a fallback, not the primary runtime source.
- Task Agent training-caption behavior now preserves WD14 tags for training workflows and uses the LLM mainly for natural-language caption assistance.
- `chain_wd14_to_anima_train_caption.json` now uses `generate_natural_caption` directly instead of the heavier `refine_wd14_tags` step, because the refine step made captions too generic in baseline testing.
- Managed KoboldCpp temp handling now uses unique per-launch temp directories and reports cleanup details.
- Prompt Studio output nodes now reinstall the editor button after existing workflow nodes are loaded, which prevents missing buttons on some ComfyUI/frontend load orders.

### Known Limitations

- Task Agent local LLM execution has a tested low-VRAM text-only baseline, but long unattended batch stability still needs more testing.
- Managed KoboldCpp auto-launch is not recommended as the default baseline yet; on the test machine the current KoboldCpp executable failed during PyInstaller self-extraction.
- Prompt Studio releases its iframe when closed, so reopening performs a clean UI load instead of retaining the previous browser session.
- Legacy Task Agent compatibility proxy nodes for old workflows are candidates for removal or hiding in a later cleanup.
- The separate gateway flow is no longer the preferred path; direct backend connection is the direction for future work.
