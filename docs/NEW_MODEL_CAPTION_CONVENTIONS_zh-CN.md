# 新模型 Caption 约定与打标映射（2026-10-24）

面向 Studio Suite 打标工作流：说明 2025 H2 之后几个热门基座/热门 LaRA 基座在**训练与微调**时 caption 长什么样，以及在本项目里该用哪个 `format_module` / 任务 bundle。

> 证据等级：标「官方」的来自模型卡、官方仓库、技术报告或官方训练文档；社区训练器会单独标注。抓不到官方说明的地方直接写「未找到官方说明」。

## 一句话结论

- 新基座（FLUX.2 / Qwen-Image / Z-Image / HunyuanImage / MOSS-Image）的文本编码器都是 LLM，caption 的原生分布是**自然语言长句**，不是 Danbooru tag 串。
- **WD14 不要丢，但降级**：保留为「第一遍召回 + 可选 tag 尾巴」，主 caption 交给自然语言长描述；带 tags 行的 `anima_train_v1` 仍然可用，但不再是把新模型当目标的默认项。
- 真正决定画质差异的是**图中文字原样引用**和**构图/光线/关系**这些 tag 表达不了的东西。

## 对照表

| 基座 | 官方 caption 形态 | 长度 | 语言 | 质量词 | 图中文字 | 本项目的 format_module |
| --- | --- | --- | --- | --- | --- | --- |
| MOSS-Image Base | 自然语言为主，布局 JSON 是可选增强 | 长描述约 120–200 词（官方分布 20/77/235 token 三档） | 英文最优 | 无 | 原样转写、保留换行 | `moss_train_nl_v1` |
| FLUX.2 dev / klein | 纯自然语言长句，触发词开头 | 官方示例约 50–80 词 | 英文 | 官方示例不用 | 引号/反引号标出 | `flux2_train_nl_v1` |
| Qwen-Image / Edit | VLM 重述的自然语言段落 | 无硬性规定（英文改写 <200 词） | 中英双语 | 训练不需要 | 引号原文、不翻译 | `qwen_image_train_nl_v1` |
| Z-Image / Turbo | 五级 caption：长/中/短/tag/模拟用户 prompt | 长 caption 可以很长 | 中英双语 | 明确禁止 | OCR 原文 + 英文双引号 | `zimage_train_nl_v1` |
| HunyuanImage 3.0 | 分层字段组合出的长描述 | 30–1000 词 | 中英双语 | 未找到规定 | 示例用引号 | `hunyuan_image_train_nl_v1` |
| LongCat-Image | 自由文本 prompt | 无规定 | 中英双语 | 未找到规定 | **必须**加引号 | `longcat_train_nl_v1` |

## MOSS-Image（本机已部署）

- 官方入口：`OpenMOSS/MOSS-Image`、HF `OpenMOSS-Team/MOSS-Image-Base` / `-SFT`、技术报告 arXiv:2610.23374。
- 输入支持三种形态并列：自然语言、tag 序列、JSON 布局；**布局不是必须的**（ComfyUI 的 `MOSSImageLayoutPrompt` 在没连框时也直接透传原 prompt）。
- 训练侧：官方**没有**发布微调数据集 schema 或训练脚本；技术报告只说「以普通自然语言描述为主，少量高质量样本额外标注 bbox JSON」。社区训练器（DiffSynth / KohakuMossTrainer）都是「一张图一条 caption」。
- 打标规范：整体构图 → 主体与动作 → 环境与光线 → 风格；画面文字原样转写、不翻译、保留换行；**不要在 prompt 里写画布尺寸**。
- 布局 JSON 的用处：文字密集、海报/多面板、需要精确排版的样本；这时用 `Region Layout` 节点的 `moss_layout` 输出，不要手算中心坐标。
- 关于 SFT：SFT 是官方默认推理权重，Base 才是社区 LoRA 起点；两者输入语法一致。

## FLUX.2

- 文本侧是 Mistral-Small-3.2-24B，官方训练文档要求「图片 + 同名 `.txt` caption」，没有 tag 字段或 JSON schema。
- 官方 style LoRA 示例的 caption 顺序：触发词 → 景别/机位 → 主体 → 姿态朝向 → 身体细节 → 前景背景。
- **style LoRA 的 caption 只描述画面内容，不写风格词**（风格靠图片本身学）。本工作流用 `caption_style_mode=style` 触发这条规则。
- 文字渲染写在引号里。

## Qwen-Image

- 官方没有 LoRA 脚本；事实标准是 Diffusers 的 `train_dreambooth_lora_qwen_image.py`，每图一条自由文本 caption。
- 技术报告的 annotation prompt 四条硬规则：自然描述、不用结构化格式；写对象属性与对象间关系；图中的文字用引号原样标出、不翻译不解释；保持真实、避免泛化。
- `Ultra HD, 4K, cinematic composition` 这类后缀是**推理端**技巧，不要写进训练 caption。

## Z-Image / Z-Image Turbo

- 文本编码器是 Qwen3-4B；官方 caption 是「长/中/短 + tags + 模拟用户 prompt」五级混合，SFT 阶段以超详细长描述为主。
- **tag 仍在官方训练分布里**，但只是五类之一，不能当唯一 caption。
- OCR 信息必须显式写进 caption，保持原语言并用英文双引号；官方 prompt enhancer 明确禁止 8K/杰作类元标签和比喻修辞。
- Turbo 是蒸馏模型，直接 SFT 会掉加速能力；要训练走 de-distill adapter 或改用非加速配置。

## HunyuanImage 3.0 / LongCat-Image

- Hunyuan：官方 caption schema 是「描述层级 + 风格属性（风格/机位/光照/氛围/构图）+ 事实实体（IP 字段）」，训练时随机组合成 30–1000 词的双语文本。官方仓库无训练代码。
- LongCat：官方提供 SFT/LoRA 训练代码，数据是 jsonl 的 `img_path / prompt`；模型卡对文字渲染有最硬规则——**必须把要渲染的文字放进引号**，否则字符级渲染会严重退化。

## 落到工作流

三种可用姿势（都在 `任务代理·资源加载器 → builtin_resource_key` 里选 `task_bundle::...`）：

1. **纯文本链路**（最快，WD14 → LLM）：`chain_wd14_to_moss_train_caption` / `chain_wd14_to_flux2_train_caption` / `chain_wd14_to_qwen_image_train_caption` / `chain_wd14_to_zimage_train_caption` / `chain_wd14_to_longcat_train_caption`。
2. **视觉链路**（质量最好，需要视觉档案）：`chain_qwen_vision_to_moss_caption`，或把 `image_captioning` 模块的 `format_module` 换成上表任意一项。
3. **要 tags 行**：继续用 `chain_wd14_to_anima_train_caption(_plain)` 与 `anima_train_v1`（第一行 WD14 原文、空行、第二行自然语言）；新基座也兼容，只是不是官方推荐主形态。

配套开关：

- `caption_style_mode=style`：style LoRA 场景，让模型有意省略风格/媒介/美学描述（FLUX.2 官方做法）。
- `caption_max_tokens`：文本链路默认 900–1200 足够覆盖 200 词以内；不要为了长而长。
- `trigger_prefix`：启动词作为描述前缀写进 caption（`mytag, <caption>`），不要挤进 tags 行。
- 禁词表在 `config/tagging_terms.json`，只过滤 LLM 侧，WD14 原文行不动。

## 未找到官方说明

- MOSS-Image：官方微调数据格式、jsonl 字段、训练脚本、SFT 专属提示词规范。
- FLUX.2：caption 长度、语言、艺术家/IP 名的明文规定（只有 style LoRA 示例与 prompting 指南）。
- Qwen-Image：官方 LoRA 脚本（Diffusers 示例是事实标准）。
- Z-Image：Z-Captioner 未开源，只有 PE 的 system prompt 公开。
- HunyuanImage 3.0：官方训练代码、质量词规定。
- Chroma：技术报告尚未发布，无 caption 规范。

## 实跑配方（2026-10-24 本机验证）

- 后端：`qwen35_9b_vision`（koboldcpp，Q4_K_M + f16 mmproj，ctx 8192），单张 2031×1425 约 **11 秒**。
- 任务：`generate_natural_caption` + `caption_output_format=plain` + `caption_style_mode=style` + `enable_image_input=true`，`max_tokens=700`。
- 触发词：`trigger_prefix` 用 `@画师名`，最终写作 `@ji, <caption>`。
- 实测两条必须同时开：只加提示词规则，9B 仍会在结尾补一句风格描述；`caption_style_mode=style` 现在会在格式化前删掉风格句（`strip_style_descriptors`），提示词里也改成了「只写主体/物体/画面布局，不要提画风、媒介、渲染、线条、配色」。
- 已知短板：9B 偶发把不确定的物体说成具体东西（例：把花丛里的暗部说成「大型黑色猫科动物」）。提示词已加「只描述确定可见的内容，模糊时用概括说法」，仍建议抽查。
- 输出约定：`xunlianji/<画师> moss/<原图同名>.png|.jpg + .txt`，txt 第一段就是 `@画师, <caption>`。

## 来源

- MOSS-Image 仓库与用户指南：<https://github.com/OpenMOSS/MOSS-Image> · <https://huggingface.co/OpenMOSS-Team/MOSS-Image-Base>
- MOSS-Image 技术报告：<https://arxiv.org/html/2610.23374>
- ComfyUI MOSS 节点与教程：<https://docs.comfy.org/built-in-nodes/MOSSImageLayoutPrompt> · <https://docs.comfy.org/tutorials/image/moss-image/moss-image>
- FLUX.2 仓库 / 训练文档：<https://github.com/black-forest-labs/flux2> · <https://docs.bfl.ml/flux_2/flux2_klein_training>
- FLUX.2 style LoRA 示例：<https://docs.bfl.ml/flux_2/flux2_klein_training_example>
- Qwen-Image 模型卡 / 仓库：<https://huggingface.co/Qwen/Qwen-Image> · <https://github.com/QwenLM/Qwen-Image>
- Qwen-Image 技术报告：<https://arxiv.org/html/2508.02324v1>
- Z-Image 仓库 / 报告：<https://github.com/Tongyi-MAI/Z-Image> · <https://arxiv.org/html/2511.22699v1>
- Z-Image prompt enhancer：<https://huggingface.co/spaces/Tongyi-MAI/Z-Image-Turbo/blob/main/pe.py>
- HunyuanImage 3.0 仓库 / 报告：<https://github.com/Tencent-Hunyuan/HunyuanImage-3.0> · <https://arxiv.org/html/2509.23951v1>
- LongCat-Image 模型卡 / 训练说明：<https://huggingface.co/meituan-longcat/LongCat-Image> · <https://github.com/meituan-longcat/LongCat-Image/blob/main/train_examples/README.md>
