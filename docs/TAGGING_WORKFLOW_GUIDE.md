# 打标工作流说明

这份文档对应当前已经落地到 `Task Agent` 主线里的打标能力。

适用目标：

- `wd14` 参考打标
- `tag + 自然语言` 混合输出
- `纯自然语言 caption`
- `NewBie XML`
- `结构化 JSON`

## 一句话结构

统一分成两层：

1. 标注理解层
   - 看图
   - 吃 `wd14` 标签
   - 吃本地 Danbooru 资源
   - 产出统一中间结果

2. 标注格式化层
   - 把统一中间结果转成不同模型要的格式

## 已支持的任务模组

放在 `任务代理·任务模组拼装器`：

- `extract_tags_from_image`
  - 直接看图打标
  - 推荐同时提供 `image_path`

- `refine_wd14_tags`
  - 用 `wd14` 原始标签做参考打标
  - 推荐同时提供 `raw_tags + image_path`

- `generate_natural_caption`
  - 基于标签和图片补自然语言 caption
  - 适合接在 `refine_wd14_tags` 后面

- `translate_anime_tags`
- `expand_anime_tags`
- `normalize_anime_tags`
  - 这三个仍然保留，用于普通 tag 工具链

## 已支持的格式模块

放在 `任务代理·任务模组拼装器 -> format_module`：

- `generic_tag_model`
  - 通用纯 tag 输出

- `anima_v1`
  - `Danbooru tags + 自然语言补充`

- `anima_train_v1`
  - 训练标注格式
  - 第一段 `tags`
  - 空一行
  - 第二段 `natural language`
  - 不追加质量词和负面词

- `illustrious_xl_v01`
  - 光辉格式

- `illustrious_train_v1`
  - 光辉训练标注格式
  - `tags + 空行 + 自然语言`
  - 不追加质量词和负面词

- `noobai_xl_1_1`
  - NoobAI 格式

- `noobai_train_v1`
  - NoobAI 训练标注格式
  - `tags + 空行 + 自然语言`
  - 不追加质量词和负面词

- `flux_natural_language_v1`
  - 纯自然语言 caption

- `flux_train_nl_v1`
  - Flux 训练标注格式
  - 只输出自然语言

- `newbie_exp01`
  - 输出结构化 XML

- `newbie_train_xml_v1`
  - NewBie 训练标注格式
  - 只输出 XML

- `structured_json_v1`
  - 输出结构化 JSON 字符串

- `structured_json_train_v1`
  - 训练用结构化 JSON
  - 不追加质量词和负面词

- `moss_train_nl_v1` / `flux2_train_nl_v1` / `qwen_image_train_nl_v1` / `zimage_train_nl_v1` / `hunyuan_image_train_nl_v1` / `longcat_train_nl_v1`
  - 新基座训练标注：只输出一段英文自然语言 caption，不带 tags 行、质量词和负面词
  - 各档的长度、结构与文字转写规则按对应官方模型卡收紧，详见 `docs/NEW_MODEL_CAPTION_CONVENTIONS_zh-CN.md`
  - `caption_style_mode=style` 时额外省略风格/媒介/美学描述（FLUX.2 style LoRA 官方做法）

## 推荐工作流

### 1. WD14 参考打标 -> Anima

- `WD14 Tagger` 输出文本
- 文本接到 `任务代理·标签工具 -> text_input`
- `任务代理·图片路径桥接` 输出 `image_path`
- `image_path` 接到 `任务代理·上下文拼装`
- `任务代理·任务模组拼装器`
  - `task_module_1 = refine_wd14_tags`
  - `task_module_2 = generate_natural_caption`
  - `format_module = anima_v1`

### 2. WD14 参考打标 -> Flux 纯自然语言

- `task_module_1 = refine_wd14_tags`
- `task_module_2 = generate_natural_caption`
- `format_module = flux_natural_language_v1`

### 3. 直接看图打标 -> XML

- `task_module_1 = extract_tags_from_image`
- `format_module = newbie_exp01`

### 4. WD14 参考打标 -> 结构化 JSON

- `task_module_1 = refine_wd14_tags`
- `task_module_2 = generate_natural_caption`
- `format_module = structured_json_v1`

### 5. WD14 拼接纯自然语言（2026-10-15 新增）

- 任务 bundle：`resources/task_bundles/chain_wd14_to_anima_train_caption_plain.json`
- `fixed_inputs` 内置 `caption_output_format=plain`：LLM 输出 3-6 句英文 prose，跳过 JSON 解析
- `caption_max_tokens` 可在 fixed_inputs 覆盖（默认 1200）
- 产出仍是两行式：第一行 WD14 原文，空行，第二行纯自然语言（可加 `trigger_prefix` 启动词前缀）
- 禁词（IP 角色/画师/signature 等）在 `config/tagging_terms.json` 配置，只过滤 LLM 侧；WD14 层过滤用其 `exclude_tags` 槽位
- 掉格式观测：节点 `status` 带 `ok / repaired / fallback` 标记

### 6. 新基座纯 caption（2026-10-24 新增）

- 任务 bundle：`chain_wd14_to_moss_train_caption` / `..._flux2_...` / `..._qwen_image_...` / `..._zimage_...` / `..._longcat_...`
- 视觉链路：`chain_qwen_vision_to_moss_caption`（`image_captioning` 直接读图）
- 产出只有一段英文 caption；WD14 只作为写作事实来源，不写进结果
- 需要 tags 行时仍用第 5 种两行式，两者按数据集目标二选一，不要混用
- 新基座的 caption 更依赖「图中文字原样引用」；没有接图时，工作流会自动去掉这些转写规则，避免模型编造文字

### 7. 画风 LoRA 的 caption 卫生（2026-10-25 新增）

画风 LoRA 的 caption 目标是「把画面内容说清楚，风格交给图片本身学」，实践中有两处会自动清理：

- `caption_style_mode=style`：提示词要求只写主体/物体/画面布局，不写画风、媒介、渲染、线条、配色；格式化层还会按句删掉漏网的风格句（`strip_style_descriptors`）。
- 主题/氛围解读句：`strip_interpretive_sentences` 会删掉「The overall composition evokes themes of…」这类整句，但只裁掉 `, creating … atmosphere.` 这样的附属从句，不会连带删掉句子里列举的物体。
- 视觉链路优先：`chain_image_to_moss_style_caption`（读图）比「WD14 → 文本 LLM」准确得多；纯文本链路容易把不确定的物体写具体。
- 训练前跑 `scripts/audit_caption_dataset.py <目录> --trigger @画师`：会报缺启动词、过短/过长、风格词、解读句、禁词、重复 caption，有问题的文件直接列出来，退出码非 0 可以作为训练前的闸门。

## 推荐资源

适合通过 `任务代理·资源加载器` 接入：

- `resource::danbooru_character_aliases`
- `resource::danbooru_character_aliases_generated`
- `resource::character_alias_safety`
- `resource::danbooru_character_webui_normalized`
- `resource::danbooru_tag_count_stats`
- `resource::danbooru_clothing_jsonl`

说明：

- 角色别名资源用于规范角色 tag
- `danbooru_character_webui.normalized.jsonl` 用于角色词典参考
- `tag_count_tags_统计.jsonl` 用于 tag 热度参考
- 服装词典只在服装相关图像上建议接入

## 当前输出结构

`任务代理·标签工具` 的 `json_text` 里，当前会尽量补出这些字段：

- `normalized_tags_en`
- `expanded_tags_en`
- `character_tags_en`
- `appearance_tags_en`
- `outfit_tags_en`
- `expression_tags_en`
- `pose_tags_en`
- `camera_tags_en`
- `style_tags_en`
- `quality_tags_en`
- `negative_tags_en`
- `caption_short_en`
- `caption_long_en`
- `natural_language_en`

不同 `format_module` 只是对这些字段做不同格式化。

## 当前限制

- 视觉链路现在按 OpenAI 风格多模态消息发送图片
- 如果后端模型本身不支持图片输入，还是会退回文本链路
- 所以要做真正看图打标时，必须确保你选的本地模型和后端支持视觉
