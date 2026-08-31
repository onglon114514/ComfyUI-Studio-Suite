# ComfyUI Studio Suite 节点使用手册

这份手册说明：要完成某个任务应该放哪些节点、怎样连线、关键字段填什么，以及哪些内部节点不应该手动添加。

## 1. 按用途选择入口

| 目标 | 推荐入口 | 需要 LLM |
| --- | --- | --- |
| 手写、点选、整理正负提示词 | `Prompt Studio 正向/反向/双提示词输出` | 否 |
| 中文转英文、扩写、规格化 | `任务模组拼装器 + 上下文拼装 + 本地LLM文本工具` | 是 |
| 使用 LM Studio、vLLM、KoboldCpp 或 API | `任务模组拼装器 + 上下文拼装 + 标签工具` | 是 |
| WD14 加自然语言训练描述 | `WD14 + Task Agent + Independent Queue` | 是 |
| 批量处理文件夹 | `Independent Prompt Folder Queue` | 可选 |
| 裁剪、训练分桶 | `智能填充裁剪缩放` 或 `分桶裁剪缩放` | 否 |
| 比较模型、LoRA、强度、采样器 | `Studio Suite XY` 参数入口链 | 否 |

第一次安装建议依次验证 Prompt Studio、纯文本 smoke test、批量队列，最后再搭 XY。

## 2. Prompt Studio

三种输出节点：

- `Prompt Studio 正向输出`：正向 STRING。
- `Prompt Studio 反向输出`：反向 STRING。
- `Prompt Studio 双提示词输出`：正向和反向 STRING。

STRING 通常连接到 `CLIP Text Encode`。若目标文本框没有输入端口，右键 widget，选择“转换为输入”。

编辑器支持分组 tag、自定义 tag、历史收藏、LoRA/embedding 列表、LoRA 信息和 NewBie XML。用户数据位于：

```text
prompt_studio/storage
```

翻译边界：Danbooru tag 和 IP 角色名优先走本地词典；普通句子使用本地 LLM；提示词扩写、角色 tag 校正和模型格式化使用 Task Agent 模组。工具栏会分别显示词典、模型和 ComfyUI 队列状态。正在排队或生图时翻译按钮会被禁用，避免抢资源。

如果已经下载 `resources/danbooru_character_webui.normalized.jsonl`，在节点目录运行一次以下命令，才能启用大型角色索引：

```powershell
python scripts/build_local_dictionary_index.py
```

## 3. Task Agent 主线

推荐把任务定义与模型执行分开：

```text
任务代理·任务模组拼装器 ── task_bundle_json ──┐
任务代理·资源加载器 ───── resource_bundle_json ─┼→ 任务代理·上下文拼装
系统提示词 / 角色卡 / 世界书 / 正则 ────────────┘
                                                    │
                                      context_bundle_json
                                                    │
                     ┌──────────────────────────────┴──────────────────────────────┐
                     ↓                                                             ↓
          任务代理·本地LLM文本工具                                      任务代理·标签工具
          无外部后端、纯文本优先                                      外部后端/API/视觉/高级配置
```

真正要处理的文本填写或连接到执行节点的 `text_input`。上下文拼装负责规则、资源和任务，不代替实际输入文本。

### 3.1 任务模组

| 模组 | 作用 |
| --- | --- |
| `translate_anime_tags` | 中文与英文 anime/tag 翻译 |
| `expand_anime_tags` | 根据原意补充绘图细节 |
| `normalize_anime_tags` | 去重、规范 tag、修正角色名和格式 |
| `extract_tags_from_image` | 视觉模型直接看图提取信息 |
| `refine_wd14_tags` | 参考 WD14 修正 tag；训练基线通常不用 |
| `generate_natural_caption` | 根据输入 tag 补自然语言 caption |

`task_module_count` 决定启用几个任务槽，只选择实际需要的任务。

常用格式模块：

| 格式 | 用途 |
| --- | --- |
| `anima_v1` | Anima 绘图提示词，tag + 自然语言 |
| `anima_train_v1` | Anima 训练 caption，无绘图质量词 |
| `illustrious_xl_v01` | Illustrious 绘图提示词 |
| `noobai_xl_1_1` | NoobAI 绘图提示词 |
| `flux_natural_language_v1` | Flux 类纯自然语言提示词 |
| `newbie_exp01` | NewBie XML |
| `structured_json_v1` | 结构化 JSON |

### 3.2 常用组合

中文描述转 NoobAI：

```text
translate_anime_tags
expand_anime_tags
normalize_anime_tags
format_module = noobai_xl_1_1
translate_direction = zh_to_en_tags
```

中文描述转 Anima：

```text
translate_anime_tags
expand_anime_tags
normalize_anime_tags
format_module = anima_v1
```

只规格化、不扩写：

```text
normalize_anime_tags
format_module = 目标模型格式
```

WD14 原 tag 完全保留，只补 Anima 训练自然语言：

```text
generate_natural_caption
format_module = anima_train_v1
```

这条训练链不要加入 `refine_wd14_tags`，否则 LLM 可能删除 WD14 识别出的角色名。

### 3.3 资源加载器和上下文

`resource_source=builtin_catalog` 从内置目录选择；`custom_path` 读取外部 JSON、JSONL、TXT、MD 等文件。

常用资源：

- `resource::danbooru_character_aliases`：规范角色 tag。
- `resource::character_alias_safety`：避免普通物品误识别为角色。
- `resource::danbooru_clothing_jsonl`：服装 tag 与说明。
- `task_bundle::chain_zh_to_anima_prompt`：Anima 预设链。
- `task_bundle::chain_zh_to_noobai_prompt`：NoobAI 预设链。

多个资源先接 `任务代理·资源包合并`，再接上下文拼装。大型词典不要整份直接塞入上下文，否则会 token 超限。

推荐连接：

```text
任务模组拼装器 task_bundle_json -> 上下文拼装 task_bundle_json
资源加载器/资源包合并 resource_bundle_json -> 上下文拼装 resource_bundle_json
上下文拼装 context_bundle_json -> 执行节点 context_bundle_json
```

路径字段优先于同类文本框。世界书和正则可启用多个槽；空槽不生效。

### 3.4 选择执行节点

`任务代理·本地LLM文本工具` 只做纯文本，直接使用节点私有 llama.cpp Python，不需要外部服务，适合第一次 smoke test。

`任务代理·标签工具` 支持私有 worker、in-process、KoboldCpp、llama.cpp server、LM Studio、vLLM 和 OpenAI 兼容 API。视觉、多后端或服务器部署使用它。`gateway_url` 是旧兼容字段，直接后端模式不需要启动旧网关。

本机纯文本起步参数：

```text
model_source = profile_catalog
context_size = 1024 或 4096
n_gpu_layers = 0（稳定优先）或按显存逐步提高
n_batch = 128 或 256
auto_load_backend = true
unload_after_run = true（后面紧接生图时）
```

## 4. 图片和视觉输入

```text
IMAGE -> 任务代理·图片路径桥接 -> image_path -> 任务代理·上下文拼装
```

启用自动清理，合理设置保留数量和年龄。只有模型、mmproj 和后端都支持视觉时，图片才会参与推理；纯文本模型会退回文本任务。

## 5. Independent 批量队列

```text
Independent Prompt Folder Queue（调度器）

Independent Load Image Path -> 主处理工作流 -> Independent Result Writer (Proxy)
```

规则：

- 父工作流只添加 `Independent Result Writer (Proxy)`。
- 不要手动添加 `Independent Result Writer (Internal)`。
- 只有一个 Load Image Path 和 Writer Proxy 时，编号填 `0` 自动识别。
- 主结果接 Writer Proxy，不要并联普通 Save Image。
- 先用 `dry_run=true` 检查数量。
- 长批量建议 `cleanup_after_save=true`。

每张图会成为一个独立 ComfyUI prompt，避免单次循环累积显存和执行状态。

## 6. 图像预处理和训练分桶

`智能填充裁剪缩放` 输出固定尺寸，适合工作流统一画布。

`分桶裁剪缩放` 在 `bucket_specs` 中一行一个 `宽x高`，选择与输入图比例最接近的桶并裁剪缩放：

```text
768x1344
832x1216
896x1152
1024x1024
1152x896
1216x832
1344x768
```

`分桶长宽整数` 使用同一份桶列表，但只输出选中桶的宽、高整数。

## 7. XY 测试

新版 XY 把主生成链和调度链分开。

主生成链：

```text
XY Parameter Input(model_name) -> 模型加载器模型名
XY Parameter Input(lora_name) -> LoRA Loader 文件名
XY Parameter Input(lora_model_strength).float -> LoRA Loader model strength
XY Parameter Input(lora_clip_strength).float -> LoRA Loader clip strength

模型 -> LoRA -> 采样 -> VAE 解码 -> Independent Result Writer (Proxy)
```

下拉框和数值 widget 先右键“转换为输入”。字符串 `value_json` 必须是合法 JSON：

```text
"anima\\anima_baseV10.safetensors"
```

数字直接写 `1.0`。

调度链：

```text
XY Axis - Parameter Slot（模型列表） -> XY Matrix X
XY Axis - LoRA Compare（LoRA × 强度） -> XY Matrix Y
XY Matrix matrix_json -> XY Queue
```

轴和 Parameter Input 通过相同 `slot_name` 关联，不需要节点编号。每个 slot 必须唯一。

LoRA 强度范围示例：

```text
strength_mode = range
start_strength = 0.5
end_strength = 1.25
strength_steps = 4
```

实际强度为 `0.5、0.75、1.0、1.25`。总任务数是“模型数 × LoRA 数 × 强度档数”。

Queue 规则：

- 唯一 Writer Proxy 时 `writer_node_id` 留空。
- `auto_build_grid=true` 会在最后自动生成图表。
- 不要手动添加 `Studio Suite XY Grid Finalizer (Internal)`。
- `XY Grid Builder` 只用于重做旧 manifest。
- 正式运行前先 `dry_run=true` 检查格子数。

旧 `target_node_id`、Target Bridge、LoRA File 和 Generic 轴仅用于兼容旧工作流。

## 8. 清理和显存

后面紧接绘图模型时，执行节点使用 `unload_after_run=true`，或在 LLM 与采样器之间接 `任务代理·结束清理`。纯文本批量打标可以保持 LLM 常驻，最后统一清理。

翻译器和 LLM 都不应在 ComfyUI 有待执行/正在执行队列时抢占资源。

## 9. 不要手动添加

- `Independent Result Writer (Internal)`
- `Studio Suite XY Grid Finalizer (Internal)`

它们由调度器自动写入子任务。手动添加会提前保存、重复保存或提前拼图。

## 10. 常见错误

`Expected exactly one XY Parameter Input with slot ... found 0`：轴与 Parameter Input 的 slot 不一致，或入口未保存在工作流。

`found 2`：同一个 slot 出现两次。

XY 没运行：检查 Queue 已启用、`dry_run=false`、Matrix 已连接、图片已接唯一 Writer Proxy。

图表提前生成或缺图：删除手动 Grid Finalizer，只保留 Queue 的 `auto_build_grid`。

token 超过 context：减少资源、世界书和任务模组，不要整份塞入大型 CSV。

Prompt Studio 按钮缺失或空白：浏览器强制刷新，停用旧 WeiLin 节点，然后运行：

```powershell
python scripts/doctor_release.py
```

## 11. 延伸文档

- `docs/QUICK_START_zh-CN.md`：安装与 smoke test。
- `docs/TAGGING_WORKFLOW_GUIDE.md`：打标任务链。
- `docs/TAGGING_STABLE_BASELINE.md`：低显存打标基线。
- `docs/xy_matrix_nodes.zh-CN.md`：XY 详细字段。
- `docs/BACKEND_SWITCH_GUIDE.md`：后端切换。
- `docs/HUGGINGFACE_RESOURCES.md`：大型资源。
