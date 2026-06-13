# Single-Run Context Guide

这套节点的核心目标不是做长记忆对话，而是做 **单次执行、高前置信息、高质量输出** 的 LLM 工具链。

推荐把一次任务拆成四层：

- `task_modules`
  - 描述这次要做什么步骤，例如中文转英文、扩写、规范化、WD14 标签校正、自然语言打标。
- `format_module`
  - 描述最终输出要符合哪个模型或训练用途，例如 `anima_v1`、`noobai_xl_1_1`、`anima_train_v1`、`flux_train_nl_v1`。
- `resource_modules`
  - 描述本次任务允许引用哪些本地资料，例如 Danbooru 角色词典、tag 频次、服装词典、画师列表。
- runtime context
  - 描述本次任务的额外要求，例如 system prompt、角色卡、世界书、正则规则、项目备注、图片路径。

## 推荐节点连接

主线工作流：

1. `任务代理·任务模组拼装`
2. `任务代理·资源加载器`，可选，可多个
3. `任务代理·资源包合并`，可选，当你需要同时挂多个资源时使用
4. `任务代理·上下文拼装`
5. `任务代理·标签工具`

连接方式：

- `任务模组拼装.task_bundle_json` -> `上下文拼装.task_bundle_json`
- `资源加载器.resource_bundle_json` -> `上下文拼装.resource_bundle_json`
- 多个 `资源加载器.resource_bundle_json` -> `资源包合并.resource_bundle_json_*` -> `上下文拼装.resource_bundle_json`
- `上下文拼装.context_bundle_json` -> `标签工具.context_bundle_json`
- `标签工具.text_input` 填本次真正要处理的原始输入

旧版 `task_config_path` 仍可用，但更适合单任务模板；复杂链路建议使用 `任务模组拼装`。

也可以直接加载预置任务链：

1. `任务代理·资源加载器.resource_source` 选 `builtin_catalog`。
2. `builtin_resource_key` 选 `task_bundle::chain_...`。
3. 输出接到 `上下文拼装.resource_bundle_json`。

## 常用预置组合

中文描述生成 Anima 绘图 prompt：

- `task_module_1`: `translate_anime_tags`
- `task_module_2`: `expand_anime_tags`
- `task_module_3`: `normalize_anime_tags`
- `format_module`: `anima_v1`
- `translate_direction`: `zh_to_en_tags`
- `style_hint`: `anime, danbooru tags with short natural-language support, galgame portrait`

中文描述生成 NoobAI prompt：

- `task_module_1`: `translate_anime_tags`
- `task_module_2`: `expand_anime_tags`
- `task_module_3`: `normalize_anime_tags`
- `format_module`: `noobai_xl_1_1`
- `translate_direction`: `zh_to_en_tags`
- `style_hint`: `danbooru tags, noobai xl 1.1, quality tags at end`

WD14 标签转训练用 Anima caption：

- `task_module_1`: `refine_wd14_tags`
- `task_module_2`: `generate_natural_caption`
- `format_module`: `anima_train_v1`
- `style_hint`: `training caption, keep wd14 tags, add concise natural-language line`

纯规格化已有标签：

- `task_module_1`: `normalize_anime_tags`
- `format_module`: 选择目标模型，例如 `anima_v1`、`noobai_xl_1_1`

## 资源模块使用原则

内置资源通过 `任务代理·资源加载器` 的 `builtin_catalog` 选择。

常用资源：

- `task_bundle::chain_zh_to_anima_prompt`
  - 中文描述到 Anima 最终 prompt 的预置任务链。
- `task_bundle::chain_zh_to_noobai_prompt`
  - 中文描述到 NoobAI prompt 的预置任务链。
- `task_bundle::chain_zh_to_illustrious_prompt`
  - 中文描述到 Illustrious XL prompt 的预置任务链。
- `task_bundle::chain_wd14_to_anima_train_caption`
  - WD14 标签到 Anima 训练 caption 的预置任务链。

- `resource::danbooru_character_aliases`
  - 手工角色别名覆盖层。
- `resource::danbooru_character_aliases_generated`
  - 大规模角色别名字典。
- `resource::danbooru_character_webui_normalized`
  - 角色结构化词典。
- `resource::danbooru_tag_count_stats`
  - tag 频次统计。
- `resource::danbooru_clothing_jsonl`
  - 服装结构化词典。
- `resource::danbooru_artist_wildcard_text`
  - 画师 tag / 风格参考。

注意：大资源不应该整份塞进 prompt。当前运行时会把资源模块作为“可用资源说明”和局部词典检索依据；后续扩展应继续走检索/召回，而不是把大文件全文注入上下文。

## 扩展规则

社区扩展时优先新增 JSON 资源，而不是改 Python 代码。

建议扩展点：

- 新任务链：用 `任务代理·任务模组拼装` 或新增 task bundle JSON。
- 新格式规范：扩展 `format_module` 约定，并在 prompt/profile 里补说明。
- 新参考资料：放入 `resources`，通过 `任务代理·资源加载器` 的 `custom_path` 或后续内置 catalog 暴露。
- 新模型适配：优先新增 backend profile，不要把模型路径写死在节点逻辑里。

不建议：

- 把某个模型、某个工作流、某个角色库硬编码进主节点。
- 把超大词典全文塞入 system prompt。
- 用长对话记忆代替本次任务需要的明确上下文。

## 当前实现边界

- 任务链会按顺序执行，上一步输出会覆盖下一步的主输入字段。
- `llama_cpp_python_inproc` 当前适合纯文本任务；视觉任务仍建议外部后端或后续单独适配。
- 大型 Danbooru 资源目前主要用于角色别名、tag 频次、服装候选等本地辅助，不等同于完整 RAG 系统。
- 发布版应保留接口和示例，不应把公司内部工作流写死为唯一用法。
