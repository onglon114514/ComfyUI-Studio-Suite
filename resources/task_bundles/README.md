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
- `chain_wd14_to_moss_train_caption.json`
  - WD14 -> MOSS-Image 训练 caption（120-200 词英文长描述，无 tags 行，不写画布尺寸）。
- `chain_wd14_to_flux2_train_caption.json`
  - WD14 -> FLUX.2 训练 caption（50-100 词，景别机位->主体->姿态->细节->前景背景）。
- `chain_wd14_to_qwen_image_train_caption.json`
  - WD14 -> Qwen-Image 训练 caption（自然描述、不用结构化格式、图中文字加引号）。
- `chain_wd14_to_zimage_train_caption.json`
  - WD14 -> Z-Image 训练 caption（120-220 词密集描述，OCR 原文加双引号，禁 8K/杰作）。
- `chain_wd14_to_longcat_train_caption.json`
  - WD14 -> LongCat-Image 训练 caption（要渲染的文字必须带引号）。
- `chain_qwen_vision_to_moss_caption.json`
  - 视觉链路版 MOSS caption：读图直接写整段描述，文字原样引用。
- `chain_image_to_moss_style_caption.json`
  - 画风 LoRA 专用：读图写 MOSS caption，`caption_style_mode=style` + 只写可见内容，画风/媒介/氛围/主题解读会被提示词和格式化层双重过滤。
- 这批新 bundle 的规则来源与适用边界见 `docs/NEW_MODEL_CAPTION_CONVENTIONS_zh-CN.md`；需要 tags 行时继续用 `chain_wd14_to_anima_train_caption(_plain)`。

扩展时可以新增同结构 JSON，不需要改 Python 代码。
