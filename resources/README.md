# Resources

This directory contains lightweight built-in resources and optional slots for large Danbooru-related resources.

## Bundled Lightweight Resources

- `danbooru_character_aliases.json`
  - Small manually curated character alias map.
  - Used to prevent common character names from being mistranslated into generic English words.

- `character_alias_safety.json`
  - Safety and filtering rules for character alias matching.
  - Helps suppress known bad matches such as meme tags or overly generic aliases.

- `task_templates/`
  - Small JSON task templates for translation, expansion, normalization, and model-specific prompt formatting.

- `task_bundles/`
  - Reusable task chains for common workflows, such as Chinese-to-NoobAI prompt generation and WD14-to-Anima training captions.

- `Danbooru服装查询资源_层级版_2026-05-26.txt`
  - Clothing hierarchy notes for outfit prompt generation.

- `Danbooru服装查询资源_本地版_2026-05-26.jsonl`
  - Structured clothing dictionary for future retrieval and clothing-tag workflows.

- `服装生成模板_system_legacy.md`
  - Legacy outfit generation prompt template kept for compatibility and reference.

## Optional Large Resources

The following files are useful, but they are intentionally ignored by Git by default. Host them on Hugging Face Datasets, GitHub Releases, or another large-file distribution channel:

- `danbooru_character_aliases.generated.json`
- `danbooru_character_webui.normalized.jsonl`
- `danbooru_character_webui.xlsx`
- `danbooru_tags_cooccurrence.csv`
- `danbooru_artist_wildcard-D站画师列表.txt`
- `tag count.xlsx`
- `tag_count_tags_统计.json`
- `tag_count_tags_统计.jsonl`
- `tag_count_channels_统计.json`
- `tag_count_channels_统计.jsonl`

After downloading optional resources, place them in this directory with the exact filenames above. The built-in resource catalog will detect files that exist and expose them in the ComfyUI resource loader node.

## Practical Guidance

- Keep small hand-curated resources in Git.
- Keep large generated resources outside Git.
- Do not feed huge text resources directly into the LLM prompt. Use resource-loader path mode or retrieval-oriented logic instead.
- If a workflow fails with a context-window error, remove large text resources from inline context and use smaller task bundles.
