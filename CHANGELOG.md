# Changelog

## Next preview

This is the local development line intended for the next GitHub push after `v0.1-preview`.

### Added

- Added a LoRA / Extra Networks drawer to each WebUI prompt editor (`frontend/index.html`): reads ComfyUI's `loras` and `embeddings` through `/weilin/physton_prompt/get_extra_networks`, shows preview thumbnails (placeholder with the name when missing), groups entries by their model subfolder, supports search, signed weights (`+1.1` / `-1.1`) and inserts `<lora:...:weight>` or embedding names into the editor's own prompt at the cursor.
- Added a backend-agnostic Prompt Studio translation core (`prompt_studio/translation_core.py`): prompt building, tag splitting, per-item retry and a shared LRU cache now serve every translation backend.
- Added an in-process CPU translation backend (`prompt_studio/inprocess_translation_runtime.py`) for standard-quant HY-MT GGUFs (Q4_K_M / Q6_K / Q8_0), with idle unload and a new `inprocess` engine option. HY-MT STQ remains the faster optional path.
- Added `scripts/test_translation_core.py` and `config/inprocess_translation.example.json`.
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
- Added named `XY Parameter Input` nodes so generation graphs can be wired normally while the scheduler injects values by slot name.
- Added a LoRA Compare axis with built-in start strength, end strength, and inclusive step expansion.
- Added automatic XY grid finalization after the last child prompt; the manual Grid Builder remains available for rebuilding old manifests.
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
- Added a task-oriented Chinese node manual and a local Prompt Studio translation guide.
- Added disk-backed Danbooru character alias lookup for Prompt Studio, with a one-time SQLite index builder for the optional large character resource.
- Added `canvas_width` / `canvas_height` INT outputs to Region Layout, so the canvas size set in the node can drive the empty-latent width/height directly.
- Added caption profiles and task bundles for the 2025-2026 base models: `moss_train_nl_v1`, `flux2_train_nl_v1`, `qwen_image_train_nl_v1`, `zimage_train_nl_v1`, `hunyuan_image_train_nl_v1`, `longcat_train_nl_v1`, each with the official length/structure/text-transcription rules; `caption_style_mode=style` drops style descriptors for style LoRAs, and image-transcription rules only apply when an image is actually attached. See `docs/NEW_MODEL_CAPTION_CONVENTIONS_zh-CN.md`.
- Expanded `config/tagging_terms.json` banned franchises so LLM-written captions stop leaking recent IP names.
- Training captions now drop mood/theme commentary: `strip_interpretive_sentences` removes whole interpretive sentences and trims `, creating … atmosphere.` clauses without losing the objects the sentence lists.
- Added `chain_image_to_moss_style_caption` for style-LoRA captioning (image-first, `caption_style_mode=style`).
- Added `chain_image_to_moss_caption` for image-first reverse captioning with a vision profile; the module uses `generate_natural_caption`, `input_field=image_path`, and plain output.
- In-process vision tagging now attaches the image when the selected profile declares `supports_vision`/`mmproj_path`, and strips Qwen `</think>` blocks before parsing.
- Task Agent unloads the in-process LLM after tagging when the same prompt also contains an image-generation sampler/loader, so tag-then-draw workflows do not run out of VRAM.
- The in-process loader now adds torch and the installed llama_cpp DLL directories, so the newer public llama-cpp-python build loads without the private runtime.
- Added `scripts/audit_caption_dataset.py` to check a training caption folder for missing trigger prefixes, word-count outliers, style words, interpretive sentences, banned terms and duplicates before a training run.
- Independent Prompt Folder Queue now has an optional `include_subfolders` switch for two-level artist folders, reports how many subfolders it skipped, and refuses to queue when duplicate image stems would overwrite outputs.
- WebUI NTR graph mode now sends `image_inputs` (image/mask base64 for `WNT_ImageInput`, uploaded name for `LoadImage`); the gateway injects them into the prompt and reports `injected.images`. Masks are only sent after the user actually paints, so a blank canvas no longer overwrites the workflow default.
- Restored `workflows_graph/webui_ntr_img2img_wnt.json` and wired `WNT_ImageOutput.mask` to `WNT_ImageInput.mask` (link 112), so graph runs return the mask file as well.

### Added

- Prompt Studio now has a Tag / 自然语言 split-mode switch. Tag mode keeps the comma-based chip rules with LoRA and bracket protection; natural-language mode splits on sentence punctuation without requiring a trailing space, keeps `dark,ornate` intact, and preserves the original punctuation in the prompt. The rules are also shown in the in-page help panel; see `docs/prompt-studio-split-mode.md`.

### Changed

- WebUI prompt chips now use the same separator-aware tokenizer as the ComfyUI WeiLin editor: sentence punctuation splits natural-language input, commas stay inside sentences, and dragging/deleting chips re-joins with the original separators instead of replacing them with `, `.
- LoRA tags (`<lora:...>`, `<lyco:...>`, `<wlr:...>`) and full-width parentheses (`（）`) are now treated as atomic when splitting prompts into chips and translation units, so file names containing commas and parenthesised phrases survive editing and dragging.
- The ComfyUI WeiLin prompt editor now has a `说明` button documenting the chip split rules and LoRA syntax, including signed weights and punctuation inside LoRA names.
- WeiLin prompt chips now keep a per-token trailing separator, so sentence-ending punctuation survives the chip round trip and `中文自然语言.中文自然语言` becomes two draggable blocks instead of one. This is a local patch to the third-party plugin; see `docs/weilin-sentence-chips-patch.md`.
- HY-MT and the new in-process backend now share the same `TagTranslator` flow, so tag splitting, per-item retry and caching behave identically regardless of engine.
- Prompt Studio now pauses the ComfyUI canvas while its full-screen editor is open and destroys the iframe on close, preventing the hidden editor and graph renderer from accumulating work together.
- Prompt Studio accepts legacy editor messages only from its own same-origin iframe and caps prompt payload size before updating ComfyUI widgets.
- NewBie XML text editing now respects IME composition and synchronizes with the legacy PromptUI bridge on composition end or focus loss instead of dispatching a full update on every keystroke.
- The Task Agent monitor avoids unchanged DOM writes and uses a slower open-panel polling interval to reduce browser rendering pressure.
- Existing XY nodes now include Chinese field tooltips covering target connections, input names, line formats, queue settings, and grid output options.
- Prompt Studio now prefers `prompt_studio/storage` inside this package for autocomplete, group tags, local tag CSVs, and prompt data.
- Prompt Studio translation now separates language direction from content type, reports dictionary/model/queue state, protects known character aliases before LLM translation, and reduces idle status polling.
- Legacy disabled `weilin-comfyui-prompt-all-in-one-page-unlock` data is now only a fallback, not the primary runtime source.
- Task Agent training-caption behavior now preserves WD14 tags for training workflows and uses the LLM mainly for natural-language caption assistance.
- `chain_wd14_to_anima_train_caption.json` now uses `generate_natural_caption` directly instead of the heavier `refine_wd14_tags` step, because the refine step made captions too generic in baseline testing.
- Managed KoboldCpp temp handling now uses unique per-launch temp directories and reports cleanup details.
- Prompt Studio output nodes now reinstall the editor button after existing workflow nodes are loaded, which prevents missing buttons on some ComfyUI/frontend load orders.
- XY Queue can auto-detect a single Result Writer Proxy when `writer_node_id` is empty and preserves child-before-finalizer ordering.
- XY child prompts execute only the rewritten Result Writer sink, preventing unrelated output branches from running once per matrix cell.

### Fixed

- Prompt Studio tag translation no longer returns the translated instruction text when the input is a single tag (for example `长发`), and weight spacing such as `(Smile: 1.1)` is normalized back to `(Smile:1.1)`.
- Natural-language translation now splits on sentence endings (`.。！？!?；;` and newlines) instead of sending the whole paragraph, keeps punctuation outside each unit, and preserves pure-English units such as `1girl` instead of rewriting them. Tag lists without sentence endings keep comma splitting.
- Bilingual annotation blocks use the same sentence-aware splitter, so `中文自然语言.中文自然语言` becomes two units instead of one block ending in a period.
- Bilingual annotations now join per-sentence translations when the third-party editor renders several sentences as one comma-delimited chip, so sentence-split text never silently loses its bilingual text.

### Known Limitations

- Task Agent local LLM execution has a tested low-VRAM text-only baseline, but long unattended batch stability still needs more testing.
- Managed KoboldCpp auto-launch is not recommended as the default baseline yet; on the test machine the current KoboldCpp executable failed during PyInstaller self-extraction.
- Prompt Studio releases its iframe when closed, so reopening performs a clean UI load instead of retaining the previous browser session.
- Legacy Task Agent compatibility proxy nodes for old workflows are candidates for removal or hiding in a later cleanup.
- The separate gateway flow is no longer the preferred path; direct backend connection is the direction for future work.
