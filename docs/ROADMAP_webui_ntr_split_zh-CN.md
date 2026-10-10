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
| studio-suite | `wnt-workflow-live-20261008` | `1cfe2c9` WNT 工作流接线 |
| studio-suite | `handoff-doc-20261009` | `32f012b` 交接文档入库 |
| studio-suite | `tag-editor-leakfix-20261010` | `e69ee91` 阶段一：tippy / 监听 / 防抖 |
| studio-suite | `rerender-fix-20261011` | 阶段二：稳定 id（本次，见第 7 节） |

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

## 7. 已修复：tag 编辑器导致浏览器崩溃（2026-10-10 / 10-11）

**现象**：在 tag 编辑器里编辑标签，浏览器不止一次直接崩（Chrome 与 Edge 均可复现）。
**定性**：与浏览器品牌无关，是上游 weilin / physton 前端的真 bug。

### 阶段一 · 泄漏（2026-10-10，commit e69ee91）

- tooltip 指令只有挂载、没有 `unmounted`，每次 tag 重渲染都把 tippy 实例永久堆进全局 `$tippyList`（600 tag 时 +7212/次），堆 +85MB/次，渲染进程 OOM。
- Jk autosize 每次调用加一个永久 `input` 监听、清理被丢弃，监听器累乘。
- textarea 到 tags 的防抖 100ms 改为 400ms（打字时不再每秒重解析十次）。
- 补丁脚本：`tools/patch_bundle.cjs` 与 `check_anchors.cjs`（锚点计数不等于 1 就拒绝打）。

### 阶段二 · O(N) 全量重绘（2026-10-10，本次）

- 根因：`_onTextareaChange` 对每个 tag 调 `_appendTag` 生成**全新随机 id**，Vue 按 `:key` diff 时所有 key 变化，于是每次击键全量销毁重建、全部 tippy 重建。
- 修复：`patch_stable_ids.cjs` 两处锚点（`match-map-build` / `stable-id-reuse`），复用稳定 id，未变化的 tag 复用对象，Vue 随之复用 DOM。
- 实测：新建节点 3005 降到 2(+1)；同一 prompt 连打 settle 10ms / 4ms。

### 验收（真实 ComfyUI 8188）

- `probe_hydrate.cjs`：水合稳定后 20~26 tick，`dom` / `svg` / `tippy` / **强制 GC 后 heap** 全部持平（dom 47200 恒定、tippy 7330 恒定、heapPostGC 123~125MB 无增长）。
- `perf_probe.cjs`：MutationObserver 事件 3005 降到 1005，其中 **1000 次是节点移动（复用后重排）**，真正新建约 2+1。
- 仓库与实装 bundle SHA256 一致：`603b724d53f1eb5c2334b6801d5122a30b732f36da2a8cf9057156726a6a61f2`。

### 残留（已知、不阻塞）

1. **O(N) 的 DOM 移动仍在**：新增一个 tag 时，N=1000/3000 下 settle 约 6~13 秒，可感知卡顿（非泄漏、非崩溃源），需改架构才根治。
2. **缓存键规则**：以后改 bundle **必须同时 bump** `prompt_studio/frontend/index.html` 里的 `?v=`，否则浏览器跑旧缓存（本次已改为 `?v=studio-suite-20261010-rerender-fix`）。
3. **两份 index.html 曾长期不同步**，本次已对齐（实装 63161 升到 63899，与仓库一致）。以后改动务必两边同步。
4. 对齐后**实装实例会同时启用 loraGrid 模块**（`js/loraGrid.js` 两边均已存在，不产生 404）。

### 复现 / 验收工具

位于工作区 `tools/ss-crash-repro/`：
`probe_hydrate.cjs`（滞留与水合）、`probe_heap.cjs`（强制 GC 后堆）、`perf_probe.cjs`（churn 与 settle）、
`perf_steady.cjs` / `perf_who.cjs` / `perf_reuse.cjs` / `perf_moves.cjs`（根因级）、`func_check.cjs`、`patch_stable_ids.cjs`。

### 教训（写进规程）

> 量「是否泄漏」必须等**异步水合稳定**，并用**不变的输入**做 tick；只看节点数上涨会把水合延迟误判成泄漏。
> 量内存必须**强制 GC 后**再比，否则 GC 噪声会被误读成线性增长。
