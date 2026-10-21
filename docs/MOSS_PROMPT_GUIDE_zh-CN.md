# MOSS-Image 提示词实战指南（中文）

> 面向本机工作流：ComfyUI 0.42.3 + MOSS-Image Base（int8）+ Region Layout 节点。
> 官方依据：[MOSS-Image USERGUIDE](https://github.com/OpenMOSS/MOSS-Image/blob/main/USERGUIDE_ZH.md)、官方 skill `skills/moss-image-prompt-enhancer`（已随仓库 vendor 到 `skills/moss-image-prompt-enhancer/`，Apache-2.0）。

## 1. 先选交付形态：整段描述（Caption）还是布局（Layout）

| 场景 | 用 Caption | 用 Layout |
|---|---|---|
| 普通插画、照片、场景 | ✅ | 也行，但没必要 |
| 海报、封面、文字排版 | 文字位置不稳 | ✅ |
| 漫画分格 / 多面板 | ❌ | ✅ |
| 物体位置关系要精确 | 口述位置 | ✅ |
| 一行字必须出现在指定位置 | ❌ | ✅ |

**规则**：复杂画面 ≠ 必须用 Layout；只有"位置本身是需求"时才画框。

## 2. Caption 写法（MOSS 是"描述"模型，不是"听指令"模型）

- **描述最终画面，而不是提要求**：写 "A red bicycle leans against a brick wall at dusk, side view, warm streetlight…"，不写 "帮我画一辆自行车"。
- 写可见的东西：主体与动作、外观、场景、镜头/构图、媒介、光线、材质、配色、氛围。
- **不要质量套话**：`masterpiece, best quality, 8k, ultra detailed` 对 MOSS 无意义（也会被我们打标侧的禁词过滤）。
- **不要过程说明**：不要写"先画背景再画人物"。
- **简洁需求就写简洁**：极简 brief 不要扩写成模板化的标题/副标题/页脚。
- **画面里要出现的文字必须逐字写对**，用引号包住；引号里的句子不一定是渲染文字——区分"参考素材"和"要显示的字"。
- 中文可以写，中英混排也可以；模型在约 4K token 内表现最好。

## 3. Layout 写法（两种坐标体系，别混）

### 3.1 草稿格式（人/AI 写这个；官方 skill 的输入）

```json
{
  "width": 1536, "height": 1024,
  "bbox_coord_space": "abs",
  "bbox_format": "xyxy",
  "summary": { "caption": "整幅画面的全局描述", "meta": ["poster"], "canvas": "single" },
  "items": [
    { "label": "object", "caption": "物体的外观描述", "bbox": [176, 224, 800, 864] },
    { "label": "text", "caption": "字体/字号/颜色/排版描述", "text": "把日常，过成诗。", "bbox": [928, 272, 1408, 448] }
  ]
}
```

- `bbox = [x_min, y_min, x_max, y_max]`，**左上原点、真实像素**，范围 0..width / 0..height。
- `width/height` 必须是 **16 的倍数**，且与生成分辨率一致；1–2MP 最佳（0.25–4MP 可外推）。
- `label`：`object` / `character` / `text`。`text` 项必须带 `text`，换行用 JSON `\n`，会按真实换行渲染。
- 一个框只是"摆放标注"，不是要画边框/贴纸；物体的外观写在该项 caption 里。

### 3.2 编译后的最终 prompt（模型真正吃的）

用官方脚本把草稿编译成一行 JSON：

```bash
python skills/moss-image-prompt-enhancer/scripts/compile_prompt.py draft.json --output prompt.txt
```

输出特点：单行 JSON、`items` 扁平、整数 `abs_centralized` / `yxyx` 坐标（y 在前、画布中心为原点、负值合法）、**不含画布宽高**（宽高由工作流节点决定）。

**我们的 Region Layout 节点直接输出这种最终格式**（`moss_layout` 输出口），所以：
- 用节点画框 → 填每框 en 描述 / text 字 → `moss_layout` 直接粘进正向提示词；
- 或者用 AI 按 3.1 草稿格式写 → 跑 `compile_prompt.py` → 得到同样的东西。

坐标换算规则（编译器做，人别手算）：`[x1,y1,x2,y2]` → `[y1-H/2, x1-W/2, y2-H/2, x2-W/2]`，小数边缘先 floor 左上 / ceil 右下。

## 4. 配合 Region Layout 节点的最短路径

1. 节点 `canvas_width/height` = 你准备生成的分辨率（16 倍数，如 1024×1024 / 1216×832）。
2. 每个框：`obj` 框填英文描述；`text` 框填字体/排版描述 + 逐字文本。
3. 全局 `caption_en` 写整幅画面的场景描述（英文）。
4. 节点 `moss_layout` 输出 → 粘到 MOSS 工作流的**正向提示词**（Layout 工作流里也可以接 `MOSSImageLayoutPrompt` 的上游）。
5. 工作流分辨率必须与节点画布一致，否则框会整体偏移。
6. 参数：模板默认 `er_sde` / CFG 2.5 / 25 步；官方推荐 **CFG 3 / 50 步**（效果不足时）。负向提示词保持短：`nsfw, worst quality, bad anatomy, watermark, jpeg artifact`。SLG 默认开在第 22 层。

## 5. 文字排版专项（最容易翻车的地方）

- **逐字确认**：每个要渲染的字都在 `text` 字段，标点、大小写、换行都按你要的来。指令里引用的句子 ≠ 要渲染的文字。
- **换行自己给**：要断行就写 `\n`，别指望自动换行落在你想要的位置。
- **字号要与框匹配**：短中文标题和一段拉丁正文的footprint完全不同；框太小会糊/挤，宁可放大框或重组布局，不要缩字。
- **文本框的 caption 写"长什么样"**：字体风格、粗细、颜色、对齐、是否有底板；`text` 写"写什么"。
- **一个位置一块文字**：需要独立摆放就拆成多个 text 项；仅字体样式不同不必拆。

## 6. 三种常用模板

### 6.1 海报（海报类）
全局 caption：写媒介、配色、光线、信息层级。标题/副标题分别建 text 框，框高按字数给足。

### 6.2 角色立绘 + 名牌
object 框包人物（caption 写外观/服装/姿态），text 框放名字（短、字号大、框别太扁）。

### 6.3 漫画分格
`summary.canvas = "multi_panel"`；每格一个 object 框（caption 写该格内容），对白各自 text 框；格子边界不要用文字描述"画一个框"，位置本身由 bbox 表达。

## 7. 常见问题

| 现象 | 原因/处理 |
|---|---|
| 框位置整体偏移 | 工作流分辨率 ≠ 画布宽高；改成一致 |
| 文字糊/缺字 | 框太小或字数太多；放大框、减少字数、拆项 |
| 物体不听框 | caption 里别写死位置描述与 bbox 冲突；位置用框表达 |
| 画面灰/平 | 提高步数到 50、CFG 3；检查负向词是否过长 |
| 中文渲染差 | 短句优先；避免生僻字；给文字框足够宽度 |
| 用了 full VAE 画面异常 | MOSS 官方验证的是 `flux2-vae_small`，先用 small |

## 8. AI 协作（官方 skill）

`skills/moss-image-prompt-enhancer/` 是官方给 agent 用的 skill：让 AI 把一句想法扩写成 caption 或 3.1 的 layout 草稿，再跑 `compile_prompt.py` 生成最终 prompt。骨架要点（摘自官方 SKILL.md）：

- 保留用户的主体、数量、关系、媒介、风格、排除项和**要显示的原话**；普通创作缺口自己补，只有在会实质改变画面时才追问。
- 描述性 prose 默认英文；要渲染的文字保持原语言、原标点、原换行。
- 设计的图要"文案与图像一起构图"：阅读顺序、层级、成组关系、在目标分辨率下的可读性；写真实文案，不写 "headline here" 占位。
- 草稿与编译产物分开保存；编译失败改草稿重跑，不要手写居中 JSON 绕过校验。

## 9. 参考

- 官方 USERGUIDE（中文）：`https://github.com/OpenMOSS/MOSS-Image/blob/main/USERGUIDE_ZH.md`
- 官方 skill：`skills/moss-image-prompt-enhancer/`（本仓库 vendor，Apache-2.0，来源同上）
- ComfyUI 教程：`https://docs.comfy.org/zh/tutorials/image/moss-image/moss-image`

## 10. 两个可直接抄的完整例子

### 10.1 纯 Caption（1024×1024，普通插画）

```
A quiet late-summer evening at a small rural train station. A teenage girl in a
white summer dress and straw hat sits on a wooden bench beside the platform,
holding a glass bottle of ramune, looking toward the tracks. Warm orange sunlight
comes from the left, long shadows across the concrete platform, a rusted sign
reading "HOSHIZORA" hangs above her. Cicada shells cling to a nearby tree trunk,
weed grass pushes through cracks, distant hills fade into haze. Anime illustration,
soft painterly rendering, warm palette, gentle rim light, shallow depth of field,
eye-level medium shot.
```

（没有质量套话、没有"请画"，全是可见描述；要渲染的字 `HOSHIZORA` 已逐字给出。）

### 10.2 Layout 草稿（1024×1024 角色海报）

保存为 `draft.json`：

```json
{
  "width": 1024, "height": 1024,
  "bbox_coord_space": "abs",
  "bbox_format": "xyxy",
  "summary": {
    "meta": ["character poster", "anime illustration"],
    "canvas": "single",
    "caption": "A vertical anime character poster on a deep navy background. A girl in a white summer dress stands in the center under warm rim light, with a large cream-colored Chinese title above her and a small English subtitle below."
  },
  "items": [
    { "label": "text", "caption": "Large cream serif Chinese title, heavy weight, centered, generous letter spacing.", "text": "夏の終わり", "bbox": [96, 80, 928, 240] },
    { "label": "character", "caption": "A teenage girl in a white summer dress and straw hat, standing three-quarter view, hands behind her back, warm rim light from the right, soft shadow on the floor.", "bbox": [320, 260, 704, 980] },
    { "label": "text", "caption": "Small light-gray English subtitle in clean sans-serif, centered, wide tracking.", "text": "THE END OF SUMMER", "bbox": [96, 760, 928, 900] }
  ]
}
```

编译：

```bash
python skills/moss-image-prompt-enhancer/scripts/compile_prompt.py draft.json --output prompt.txt
```

得到的 `prompt.txt`（单行，可直接粘正向提示词；画布 1024×1024 由工作流决定）：

```json
{"summary":{"meta":["character poster","anime illustration"],"canvas":"single","caption":"A vertical anime character poster on a deep navy background. A girl in a white summer dress stands in the center under warm rim light, with a large cream-colored Chinese title above her and a small English subtitle below."},"items":[{"label":"text","caption":"Large cream serif Chinese title, heavy weight, centered, generous letter spacing.","text":"夏の終わり","bbox":[-432,-416,-272,416]},{"label":"character","caption":"A teenage girl in a white summer dress and straw hat, standing three-quarter view, hands behind her back, warm rim light from the right, soft shadow on the floor.","bbox":[-252,-192,468,192]},{"label":"text","caption":"Small light-gray English subtitle in clean sans-serif, centered, wide tracking.","text":"THE END OF SUMMER","bbox":[248,-416,388,416]}]}
```

（同一套框用 Region Layout 节点画出来，`moss_layout` 输出应当与上面等价——节点内部直接算的就是这个坐标体系。）

> 注意：`text` 里的 "夏の終わり" 是日文；想渲染中文标题就换成中文，MOSS 中英日文字渲染都支持。
