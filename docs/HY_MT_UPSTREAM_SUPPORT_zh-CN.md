# HY-MT 上游框架支持现状（调研）

更新时间：2026-09-20
调研问题：Hy-MT（腾讯混元 Hunyuan-MT 1.5 / 1.8B / 1.25bit）是否已被主流开源推理框架原生支持，能否替换当前"临时专用路径"实现。

## 1. 结论摘要

- 截至 **2026-09-20**，Hy-MT 1.25bit 所依赖的 **STQ1_0（Sherry ternary 量化，1.3125 bpw）内核仍未合并进 llama.cpp 主线**：核心 PR **#22836 处于 open 状态**（`merged_at` 为 `null`），x86 侧的 AVX2 内核 PR **#27377 同样 open**。
- 因此当前仓库"私有 `llama-server` + 实验 loader + 把官方 GGUF 里 STQ 类型 ID 从 `42` 改成 `43`"的做法，**在主线合并之前仍然是必要的临时路径**，不是可以立刻删掉的历史包袱。
- 但上游路线已经明确：PR 合并后可直接走官方链路生成 STQ1_0 模型，**不再需要私有 loader，也不再需要类型 ID 补丁**：
  `convert_hf_to_gguf.py`（bf16）→ `llama-quantize ... STQ1_0` → `llama-completion -ngl 0 --jinja`

## 2. 已核实（带证据）

### 2.1 llama.cpp — STQ1_0 基础内核

| 项 | 值 |
| --- | --- |
| PR | [#22836](https://github.com/ggml-org/llama.cpp/pull/22836) `ggml-cpu : add STQ1_0 ternary quantization with ARM NEON vec_dot kernel` |
| 状态 | **open**，`merged_at = null`（2026-09-20 经 api.github.com 核实） |
| 时间 | 创建 2026-05-08，最后更新 2026-09-14 |
| 作者 | sjl623（请求 reviewer：CISC、ggerganov） |
| 关键内容 | STQ1_0 = Sparse Ternary Quantization（Sherry 方法，ACL 2026）。每个权重 ∈ {-d, 0, +d}，每 4 个 lane 恰好 1 个为 0，**1.3125 bpw**。腾讯混元用该格式压缩 Hy-MT1.5-1.8B 并开源，PR 自述"本 PR 贡献了支撑其端侧离线翻译 App 的 1.25-bit 内核"。**目前只提供 ARM NEON 内核。** |

### 2.2 llama.cpp — STQ1_0 的 x86（AVX2）内核

| 项 | 值 |
| --- | --- |
| PR | [#27377](https://github.com/ggml-org/llama.cpp/pull/27377) `ggml-cpu : add AVX2 vec_dot kernel for STQ1_0` |
| 状态 | **open**（2026-09-20 经 api.github.com 核实） |
| 作者 | layfhaker |
| 意义 | 本机是 Windows x86（i9-13900HX / AVX2），这条正是 x86 可用性的前置条件；未合并前只能依赖实验分支构建。 |

### 2.3 与项目现状的对应

- 本机使用 `runtime/hy_mt/llama-server.exe`（支持 STQ1_0 的实验构建），并需 `scripts/patch_hy_mt_stq_type_id.py` 把官方 GGUF 的 STQ 类型 ID 由旧值 `42` 改为 loader 期望的 `43` —— 说明本机跑的是"PR 分支构建 + 私有 loader"，**不是上游发布版**。
- `docs/HY_MT_EXPERIMENT_zh-CN.md` 原本就记录了"所需支持尚未进入 llama.cpp 稳定主线"；本次核实确认截至 2026-09-20 仍然如此。

## 3. 未证实 / 待补

以下各项**本次未能取证**，不得当作结论使用：

- vLLM、SGLang、LMDeploy、transformers、Ollama、LM Studio 对 Hunyuan-MT 架构与 STQ1_0 量化的支持现状：未取证。
  （原因：本机访问 `github.com` / `huggingface.co` 直连在调研时段持续 ECONNRESET / 超时，只有 `api.github.com` 可用。）
- 上游官方 1.25bit GGUF 的 STQ 类型 ID 问题是否已被上游修复：未证实。
- 模型仓库命名不一致：PR 引用的是 `AngelSlim/Hy-MT1.5-1.8B-1.25bit`，本仓库文档引用的是 `tencent/Hy-MT1.5-1.8B-1.25bit-GGUF`。两者关系（同一模型的不同发布者 / 镜像）未证实。
- 上述两个 PR 是否有望在近期合并：无公开时间表，未证实。

## 4. 建议

1. **主线合并前（当前状态）**：保留现有实验路径，但把"来源"写清楚——在文档/配置里记录 `llama-server.exe` 是依据哪个 PR / commit 构建的，方便日后替换与复现。
2. **主线合并后**：切换到官方链路生成 STQ1_0 GGUF，删除 `scripts/patch_hy_mt_stq_type_id.py` 与私有 loader，只保留官方 `llama-server` 二进制。
3. **过渡期可加的能力探测**：启动时检测所用 `llama-server` 是否为官方构建、是否原生识别 STQ1_0；是则走官方路径（不打 42→43 补丁），否则回退现有实验路径。这样上游一旦可用无需改配置。
4. 持续跟踪 #22836 与 #27377；合并后再补 vLLM / SGLang / Ollama 等框架的复核。

## 5. 证据链接

- PR #22836：https://github.com/ggml-org/llama.cpp/pull/22836
- PR #27377：https://github.com/ggml-org/llama.cpp/pull/27377
- API 核实（2026-09-20 抓取）：https://api.github.com/repos/ggml-org/llama.cpp/pulls/22836 → `"state": "open"`, `"merged_at": null`
- API 核实（2026-09-20 抓取）：https://api.github.com/repos/ggml-org/llama.cpp/pulls/27377 → `"state": "open"`
- 相关讨论：#19123（llama.cpp 侧对该格式的诉求）；`AngelSlim/Hy-MT1.5-1.8B-1.25bit` 模型卡 discussions/3
