# HY-MT 轻量离线翻译实验

此路线用于 Prompt Studio 的中英双向翻译，目标是在不占用绘图显存的前提下，用 CPU 常驻一个小型专用翻译模型。当前实现属于实验功能，不作为默认发布依赖。

相关上游：

- [Tencent HY-MT 官方仓库](https://github.com/Tencent-Hunyuan/HY-MT)
- [Hy-MT1.5-1.8B-1.25bit-GGUF 官方模型](https://huggingface.co/tencent/Hy-MT1.5-1.8B-1.25bit-GGUF)
- [llama.cpp STQ 支持 PR](https://github.com/ggml-org/llama.cpp/pull/22836)
- [llama.cpp x86 AVX2 优化 PR](https://github.com/ggml-org/llama.cpp/pull/27377)

## 当前支持

- 后端 A（默认优先）：`Hy-MT1.5-1.8B-1.25bit` + 支持 STQ1_0 的实验版 `llama-server`，CPU、零显存、速度最快，但依赖未并入主线的私有构建和类型 ID 补丁。
- 后端 B：官方标准量化 GGUF（`Q4_K_M` / `Q6_K` / `Q8_0`）+ 进程内 `llama-cpp-python`，不需要额外二进制，但常驻内存约多 1.2 GB、加载 0.3B llama.cpp CUDA context 时多占约 400 MB 显存，解码速度约慢 25%。
- 两个后端共用 `prompt_studio/translation_core.py`：提示词构造、标签拆分、逐项重试和 512 条缓存完全一致；前端引擎选择新增 `进程内 Q4（CPU）`。
- 模型：`Hy-MT1.5-1.8B-1.25bit.gguf`（后端 A）或 `HY-MT1.5-1.8B-Q4_K_M.gguf`（后端 B）
- 设备：强制 CPU，`n_gpu_layers=0`
- 生命周期：首次调用启动，空闲超时自动退出；ComfyUI 接收绘图队列前主动退出；Windows 异常退出时由 Job Object 回收子进程（仅后端 A）
- 前端：`自动（优先 HY-MT）`、`HY-MT CPU（实验）`、`进程内 Q4（CPU）`、`通用 LLM`

## 文件位置

```text
ComfyUI-Studio-Suite/
  runtime/hy_mt/llama-server.exe
  models/hy_mt/Hy-MT1.5-1.8B-1.25bit-type43.gguf
  config/hy_mt_runtime.local.json
```

发布包不会包含模型、运行时二进制或 `*.local.json`。这些文件需要用户自行放置，或由后续的一键安装器下载。

## 修正官方 GGUF 类型 ID

当前官方 1.25bit GGUF 把 STQ 张量标记为旧类型 ID `42`，公开 PR 的当前 loader 使用 `43`，直接加载会出现张量偏移错误。保留原文件并生成兼容副本：

```powershell
python scripts/patch_hy_mt_stq_type_id.py "D:\models\Hy-MT1.5-1.8B-1.25bit.gguf"
```

脚本仅接受已知官方坏文件哈希，要求恰好找到 224 个旧 STQ 张量，并输出 `Hy-MT1.5-1.8B-1.25bit-type43.gguf`；不会原地修改下载文件。

可从示例复制本地配置：

```powershell
Copy-Item config/hy_mt_runtime.example.json config/hy_mt_runtime.local.json
```

相对路径以节点包根目录为基准，也可通过环境变量覆盖：

- `STUDIO_SUITE_HY_MT_SERVER`
- `STUDIO_SUITE_HY_MT_MODEL`

## 进程内 Q4 后端

```text
config/inprocess_translation.local.json
models/hy_mt/HY-MT1.5-1.8B-Q4_K_M.gguf
```

可从示例复制本地配置：

```powershell
Copy-Item config/inprocess_translation.example.json config/inprocess_translation.local.json
```

关键字段：`model_path`、`threads`（0 = 自动）、`context_size`、`n_batch`、`n_gpu_layers`（默认 0）、`idle_timeout_sec`、`llama_cpp_prefer_private`。模型路径也可用环境变量 `STUDIO_SUITE_HYMT_Q4_MODEL` 覆盖。

进程内后端使用 ComfyUI 自己的 `llama-cpp-python`；`llama_cpp_prefer_private=false` 时用 site-packages 里的公共轮子，`true` 时用包内 `runtime/python_libs`。它只接受非 STQ 的标准量化 GGUF——公共 `llama-cpp-python 0.3.36` 的 ggml 类型上限为 42，加载 1.25bit 的 type 43 会直接报 `invalid ggml type 43`。

本机实测（i9-13900HX，12 线程，6 例取中位）：STQ 0.448 s / 43 tok/s / 585 MB RSS / 0 显存；Q4_K_M 0.562 s / 31.8 tok/s / 1.77 GB RSS / 约 400 MB 显存。质量基本持平。

## 限制

HY-MT 1.25bit GGUF 使用 STQ1_0。当前所需支持尚未进入 llama.cpp 稳定主线，Windows x86 AVX2 优化也仍处于实验阶段。因此正式发布前必须完成长时间翻译、队列抢占、进程退出、内存释放和不同 CPU 的兼容性测试。

如果 HY-MT 文件不可用，`自动`模式会回退到现有通用 LLM；显式选择 `HY-MT CPU（实验）`时则会返回明确的缺失错误，不会静默改用其他模型。

## 本机参考性能

测试环境：Intel Core i9-13900HX、Windows、AVX2 实验分支、12 CPU 线程、`ctx=1024`。

- 冷启动并完成首句：约 2.65 秒
- 模型预热后的短句：约 0.55–0.68 秒
- Prompt：约 88–108 tokens/s
- Generation：约 55–60 tokens/s
- 常驻 RSS：约 575 MB

这些数字仅用于确认实现路线和数量级。其他 CPU、线程数、句子长度和系统负载会改变结果。
