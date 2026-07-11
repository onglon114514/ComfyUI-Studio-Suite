# Example Workflows

这些工作流用于预览版分发和内部交接。目标是让用户先跑通节点内核，再按自己的模型路径和资源需求扩展。

## noob_zh_to_en_expand_preview.json

用途：
- 中文描述转英文 Danbooru tag。
- 扩写为 NoobAI XL 1.1 风格提示词。
- 做画图用提示词生成，不是训练打标。

默认链路：
- `任务代理·任务模组拼装`
  - `translate_anime_tags`
  - `expand_anime_tags`
  - `normalize_anime_tags`
  - `format_module = noobai_xl_1_1`
- `任务代理·资源加载器`
  - `resource::danbooru_character_aliases`
- `任务代理·标签工具`
  - `backend_provider = llama_cpp_python_inproc`
  - `model_source = profile_catalog`
  - `backend_profile = gemma4_e4b_q4 | Gemma 4 E4B Q4`

使用前需要：
- 编辑 `config/backend_profiles.json`，把 `gemma4_e4b_q4.model_path` 指向本机 GGUF。
- 如果不用 `profile_catalog`，可以把标签工具的 `model_source` 改成 `custom_path`，再连接或填写模型路径。

推荐初始参数：
- `context_size = 4096`
- `llama_cpp_python_n_gpu_layers = 10`
- `llama_cpp_python_n_batch = 512`
- `unload_after_run = true`

如果这个输出会直接接到采样器或显存重的绘图节点，必须保持 `unload_after_run = true`。只有纯文本批量处理、不立刻生图时，才建议临时改成 `false` 提速。

## tagging_wd14_llm_anima_train_preview.json

用途：
- WD14 先输出可靠基础 tag。
- 本地 LLM 读取 WD14 tag，只补自然语言训练描述。
- 输出目标是 Anima 训练 caption：第一行 tag，第二行自然语言描述，不添加绘图质量词。

默认链路：
- `IndependentPromptFolderQueue`
  - 从图片文件夹排队。
- `IndependentLoadImagePath`
  - 读取队列图片。
- `WD14Tagger`
  - 输出基础 tag。
- `任务代理·任务模组拼装`
  - `generate_natural_caption`
  - `format_module = anima_train_v1`
- `任务代理·标签工具`
  - `backend_provider = llama_cpp_python_inproc`
  - 推荐先用 `model_source = profile_catalog`
  - `backend_profile = gemma4_e4b_q4 | Gemma 4 E4B Q4`

使用前需要：
- 编辑 `config/backend_profiles.json`，把 `gemma4_e4b_q4.model_path` 指向本机 GGUF。
- 把“填待打标图片文件夹路径”文本节点改成实际图片目录。
- 确认任务链只使用 `generate_natural_caption`，不要在稳定基线里加 `refine_wd14_tags`。

推荐初始参数：
- `context_size = 4096`
- `llama_cpp_python_n_gpu_layers = 0`
- `llama_cpp_python_n_batch = 128`
- `unload_after_run = false`

批量打标时如果不接采样器，保持 `unload_after_run = false`，队列结束后接 `任务代理·结束清理` 统一释放。这样比每张图反复加载模型稳定。如果同一工作流后面还有采样或大模型加载，才改成 `true` 或在采样前接清理节点。

视觉模型、图片路径桥接、`refine_wd14_tags` 可以作为实验扩展，但不属于低显存稳定基线。

## 分发注意事项

- 示例工作流不依赖大资源文件。
- 默认只使用轻量资源 `resource::danbooru_character_aliases`。
- 大资源如 `danbooru_character_aliases.generated.json`、`danbooru_tags_cooccurrence.csv`、`tag_count_tags_统计.jsonl` 属于可选增强资源，不建议直接塞进示例工作流。
- 如果出现 `llama_cpp was already imported from another location`，需要启用 `000_task_agent_llama_preload` shim 并重启 ComfyUI。
- 如果出现 `Requested tokens exceed context window`，通常是把大文本资源直接作为上下文塞进去了；应改用资源加载器的 path/resource module 模式，或减少资源。
