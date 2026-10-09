# 交接说明：webui-ntr / studio-suite 会话切分

> 本文件按仓库既有约定（`.gitignore` 忽略 `docs/HANDOFF_*.md`）保持**本地不入库**，因为它含本机路径。

日期：2026-10-08 · 切分决定：**webui-ntr 独立会话；studio-suite 交接出去**

## 1. 为什么能切

两者是**独立节点包**，运行时零强绑。真正相交的只有两处：

1. **提示词编辑器前端**（studio-suite 的 `frontend/index.html` / `prompt_studio/frontend/`）
2. **未来 OpenClaw ↔ ComfyUI 的 LLM 桥**（尚未开工，归 studio-suite 侧）

除此之外互不 import、互不依赖 -> 可以各自演进。

## 2. 仓库 / 实装位置

| 项 | 路径 |
|---|---|
| A 仓库 | `E:\114514\New project\webui-ntr` |
| A 实装 | `E:\AI\ComfyUI-aki-v2\ComfyUI\custom_nodes\comfyui_webui_ntr` |
| B 仓库 | `E:\114514\New project\studio-suite-llm-release-audit`（分支 `codex/hy-mt-experimental`） |
| B 实装 | `E:\AI\ComfyUI-aki-v2\ComfyUI\custom_nodes\comfyui_studio_suite_prompt_studio_merge` |

仓库与实装是**两份副本**，改完必须同步（webui-ntr 用 `tools/sync_wnt_nodes.py`）。

## 3. 回退点（git tag）

| 仓库 | tag | commit |
|---|---|---|
| webui-ntr | `checkpoint-20261008-initial` | `69cbea3`（根提交，6 文件） |
| studio-suite | `checkpoint-20261008-pre-webui-ntr-split` | `cf69508`（34 文件） |

回退方式（**只回文件、不动历史**）：

```
git checkout <tag> -- .        # 把工作区文件恢复到该回退点
```

禁止 `reset --hard` / 改历史 / `clean` / `push`（push 需单独授权）。

## 4. webui-ntr 现状（本会话，已实测）

- `WNT_ImageInput`   图像+蒙版注入 —— 实测 320x192 精确落盘
- `WNT_LoraStackLoader` LoRA 堆加载 —— 实测 anima_qingwei + @miratsu 出图
- `WNT_ImageOutput`  出图回传 webui —— 实测 512x512 / 396KB 落盘、`ui.images`+`ui.masks` 回传

**本轮修掉的两个真 bug（真机测试才暴露）**：
1. `WNT_ImageOutput` 缺 `OUTPUT_NODE = True` -> `prompt_no_outputs`
2. `load_lora_for_models()` 第三参要权重字典，曾误传路径字符串 -> 需先 `comfy.utils.load_torch_file(path)`

**待做**：把范例工作流 `workflows_graph/webui_ntr_img2img.json` 从 `ETN_LoadImageBase64` 老路线改接 WNT 三件套。

## 5. studio-suite 待补小功能（交接清单）

主体已完成，只剩这些：

1. **输入来源"声明"**（两条路待拍板）
   - (a) 工作流侧改约定：`ETN_Parameter` 的 type 改成 `prompt (positive)` / `prompt (negative)`
   - (b) 扫描侧兜底：按名字（正面词/负面词）+ `ETN_LoadImageBase64` 自动认领，不动工作流
2. **组开关前端验证**：`wfGroupCard` / `wfGroupPanel` 已就绪，接口 200；只差在浏览器真点一遍
3. **webui 接口续做**（起点待指认）：组开关 v2 / 图片蒙版 / param-view / upload 代理
4. **`StudioSuite_ApplyLoraStack`** 单独真跑验证（同 bug 已修，未单独跑过）
5. **ComfyUI 内对话面板**（POST 18789 `/v1/chat/completions` + `x-openclaw-model`）—— 未做
6. **打标新节点**（走工作流固定流程、后端指 OpenClaw 云 LLM）—— 未做
7. **框外观路线 A/B/C** 拍板（建议 C：原生 Subgraph）

## 5.5 新发现：`graph_to_prompt.py` 的 widget 映射有越位 bug（studio-suite 侧）

给 webui-ntr 做画布接线时，用**原生节点**校准发现的，可复现：

**根因**：`graph_to_prompt` 用"required 输入**全排列**的位置"去索引 `widgets_values`，
而 ComfyUI 只对**可做 widget 的输入**（INT/FLOAT/STRING/BOOLEAN/combo 且非 forceInput）计数 -> 出现越位。

**证据**：

| 节点 | widgets_values | 产出 | 应为 |
|---|---|---|---|
| `#32 KSampler` | `[0,"randomize",20,8,"euler","simple",1]` | `seed="randomize"` | `seed=0` |
| `#43 JoinStringMulti` | `[2," ",false,null]` | (`inputcount`/`delimiter`/`return_list` 全丢) | `inputcount=2` |
| `#98 WNT_ImageOutput` | `[true,"wnt"]` | `save_to_disk="wnt"` | `save_to_disk=true` |

**验证一致性**：`#75 WNT_ImageInput`（6 个输入**全是** widget）映射全对 —— 反证越位假设成立。

**影响范围**：
- ❌ 走该转换器生成 prompt 的执行/预览路径会拿到**错值**（如 KSampler seed、JoinStringMulti 的 inputcount）
- ✅ **ComfyUI 画布加载不受影响**（前端自己按 widget 数映射）

**修法建议**：索引时只对 `可做 widget 且非 forceInput` 的输入自增，而非对所有 required 输入自增。

## 6. 防打架规则

1. **一文件一归属**：`frontend/index.html`、`prompt_studio/frontend/**` 归 studio-suite；webui-ntr 不碰
2. **共享面改动先声明**：动提示词编辑器或 LLM 桥前，两边都要知道
3. **实装同步**：任何仓库改动 -> 同步实装 -> 校验 SHA256 -> 需重启才生效
4. **大资源不入库**：`*.sqlite` / `*.parquet` / 大 jsonl / `danbooru_tags_*` 已在 `.gitignore`
5. **临时脚本**放仓库 `tools/`（webui-ntr）或工作区 `game-project/tools/`（分析类）

## 7. 待办（HIGH）：studio-suite tag 编辑器导致**浏览器崩溃**

**现象**：在 tag 编辑器里编辑标签时，浏览器**不止一次直接崩溃**（2026-10-08 / 10-09 均复现）。

**关键观察（推翻了先前假设）**：
- 一开始在 **Chrome** 里崩 -> 换成 **Edge**，前端稳定下来；
- 但**再用编辑器时同样出问题**。

-> **与浏览器品牌无关**，此前怀疑 Chrome 属**误判**。这是 studio-suite 前端的真 bug。

**归口**：按会话切分决定，**归 studio-suite 交接会话处理**（不在 webui-ntr 会话里做）。

**排查起点建议**：
1. 定位 tag 编辑路径：`frontend/index.html`、`prompt_studio/frontend/index.html`
2. 优先怀疑**无限重渲染 / 无界 DOM 增长**（编辑一次追加一次节点、全量重排大列表）
3. 次查 **事件监听器泄漏**（每次编辑重复 addEventListener 未移除）
4. 次查 **定时器泄漏**（`setInterval`/`rAF` 未清理，编辑后仍在跑）
5. 次查 **localStorage 写入爆炸**（大 payload 同步写、每键一击）
6. 复现要点：**换浏览器仍复现** -> 与品牌无关，与编辑器交互路径强相关

**注意**：前端是**单文件原生 HTML/CSS/JS、零外部 CDN 依赖**，无第三方库干扰，定位范围相对可控。
