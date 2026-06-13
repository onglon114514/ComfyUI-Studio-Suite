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

扩展时可以新增同结构 JSON，不需要改 Python 代码。
