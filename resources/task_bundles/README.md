# Task Bundles

这里放可直接复用的任务链预设。

用法：

1. 在 `任务代理·资源加载器` 选择 `builtin_catalog`。
2. 在 `builtin_resource_key` 里选择 `task_bundle::...`。
3. 把 `resource_bundle_json` 接到 `任务代理·上下文拼装`。
4. 把 `上下文拼装.context_bundle_json` 接到 `任务代理·标签工具`。

这些文件只描述任务链，不绑定具体模型路径或后端。

当前预设：

- `chain_zh_to_anima_prompt.json`
  - 中文描述 -> 英文 tag -> 扩写 -> 规格化 -> Anima 混合 prompt。
- `chain_zh_to_noobai_prompt.json`
  - 中文描述 -> 英文 tag -> 扩写 -> 规格化 -> NoobAI prompt。
- `chain_zh_to_illustrious_prompt.json`
  - 中文描述 -> 英文 tag -> 扩写 -> 规格化 -> Illustrious XL prompt。
- `chain_wd14_to_anima_train_caption.json`
  - WD14 标签 -> 标签校正 -> 自然语言训练 caption。
- `chain_wd14_to_anima_train_caption_plain.json`
  - 上一条的纯自然语言变体：`generate_natural_caption` 的 `fixed_inputs.caption_output_format = "plain"`，第二行改为 3-6 句英文 prose（不输出 JSON），生成上限读 `caption_max_tokens`（默认 1200，见 `task_agent_core/nodes.py`）。
  - 禁词与启动词来自 `config/tagging_terms.json`；可用 `fixed_inputs.trigger_prefix` 覆盖启动词，行格式为 `trigger_prefix, caption`。
- `chain_qwen_vision_to_caption.json`
  - 视觉模型读图 -> 视觉打标 -> 自然语言 caption（不依赖 WD14；需视觉档案，见 `docs/QWEN_BACKEND_zh-CN.md`）。

扩展时可以新增同结构 JSON，不需要改 Python 代码。
