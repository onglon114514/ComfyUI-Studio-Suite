# Task Templates

这些模板用于旧版或单任务模式：

- `任务代理·上下文拼装`
  - `task_config_path`
- 或 `任务代理·文件配置加载`
  - 先读文件，再转接到 `任务代理·上下文拼装`

如果只是执行单个任务，推荐流程：

1. 在 `任务代理·上下文拼装` 里填写：
   - `task_config_path`
2. 根据需要补：
   - `system_prompt_path`
   - `character_card_path`
   - `world_book_path`
   - `regex_rules_path`
3. 把 `context_bundle_json` 连到：
   - `任务代理·标签工具`

如果是高质量单次任务链，推荐主线流程改为：

1. 用 `任务代理·任务模组拼装` 选择：
   - `translate_anime_tags`
   - `expand_anime_tags`
   - `normalize_anime_tags`
   - 或 `refine_wd14_tags` / `generate_natural_caption`
2. 用 `format_module` 选择最终目标格式：
   - `anima_v1`
   - `noobai_xl_1_1`
   - `anima_train_v1`
   - `flux_train_nl_v1`
3. 可选：用 `任务代理·资源加载器` 加载 Danbooru 角色、tag 统计、服装、画师等资源。
4. 把 `task_bundle_json` 和 `resource_bundle_json` 接到 `任务代理·上下文拼装`。
5. 把 `上下文拼装.context_bundle_json` 接到 `任务代理·标签工具`。

详细设计见：

- `docs/SINGLE_RUN_CONTEXT_GUIDE.md`

当前模板以这几类用途为主：

- 标签扩写
- 标签规范化
- 中英标签转换
- 直接生成适配不同模型范式的 prompt

另外也补了一套 **中文别名文件名**，专门给旧模板模式用。

这些文件名会直接写清楚：

- 该文件要填到哪个路径框
- 这个文件是做什么任务

例如：

- `给上下文拼装_task_config_path_中文转英文tag.json`
- `给上下文拼装_task_config_path_Anima最终提示词.json`
- `给上下文拼装_task_config_path_NoobAI规格化.json`

这样你在旧模板模式下，就不用再靠英文文件名猜应该填哪一个。

其中 `anima` 建议分两步理解：

- `tag_expand_anima_v1.json`
  - 先做 Anima 前置 tag 扩写
  - 结果以 Danbooru tag 骨架为主
- `tag_normalize_anima_v1.json`
  - 只做 Anima 前置 tag 规范化
  - 去重、纠正角色 tag、整理顺序
- `prompt_generate_anima_v1.json`
  - 做最终 Anima 混合 prompt
  - 输出是 `Danbooru tag + 短自然语言补充`

说明：

- `task_type` 决定主任务类型
- `target_profile` 决定最终格式化到哪个模型范式
- `fixed_inputs` 可预置固定输入字段
- `input_field` 决定 `任务代理·标签工具` 的 `text_input` 会被写入哪个字段

当前 `标签工具` 兼容的主任务：

- `expand_anime_tags`
- `normalize_anime_tags`
- `translate_anime_tags`
- `refine_wd14_tags`
- `generate_natural_caption`
- `generate_outfit_tags`
- `generate_character_design`
