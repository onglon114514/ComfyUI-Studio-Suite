# HY-MT 轻量离线翻译实验

此路线用于 Prompt Studio 的中英双向翻译，目标是在不占用绘图显存的前提下，用 CPU 常驻一个小型专用翻译模型。当前实现属于实验功能，不作为默认发布依赖。

相关上游：

- [Tencent HY-MT 官方仓库](https://github.com/Tencent-Hunyuan/HY-MT)
- [Hy-MT1.5-1.8B-1.25bit-GGUF 官方模型](https://huggingface.co/tencent/Hy-MT1.5-1.8B-1.25bit-GGUF)
- [llama.cpp STQ 支持 PR](https://github.com/ggml-org/llama.cpp/pull/22836)
- [llama.cpp x86 AVX2 优化 PR](https://github.com/ggml-org/llama.cpp/pull/27377)

## 当前支持

- 模型：`Hy-MT1.5-1.8B-1.25bit.gguf`
- 推理：支持 STQ1_0 的实验版 `llama-server`
- 设备：强制 CPU，`n_gpu_layers=0`
- 生命周期：首次调用启动，空闲超时自动退出；ComfyUI 接收绘图队列前主动退出；Windows 异常退出时由 Job Object 回收子进程
- 前端：`自动（优先 HY-MT）`、`HY-MT CPU（实验）`、`通用 LLM`

## 文件位置

```text
ComfyUI-Studio-Suite/
  runtime/hy_mt/llama-server.exe
  models/hy_mt/Hy-MT1.5-1.8B-1.25bit.gguf
  config/hy_mt_runtime.local.json
```

发布包不会包含模型、运行时二进制或 `*.local.json`。这些文件需要用户自行放置，或由后续的一键安装器下载。

可从示例复制本地配置：

```powershell
Copy-Item config/hy_mt_runtime.example.json config/hy_mt_runtime.local.json
```

相对路径以节点包根目录为基准，也可通过环境变量覆盖：

- `STUDIO_SUITE_HY_MT_SERVER`
- `STUDIO_SUITE_HY_MT_MODEL`

## 限制

HY-MT 1.25bit GGUF 使用 STQ1_0。当前所需支持尚未进入 llama.cpp 稳定主线，Windows x86 AVX2 优化也仍处于实验阶段。因此正式发布前必须完成长时间翻译、队列抢占、进程退出、内存释放和不同 CPU 的兼容性测试。

如果 HY-MT 文件不可用，`自动`模式会回退到现有通用 LLM；显式选择 `HY-MT CPU（实验）`时则会返回明确的缺失错误，不会静默改用其他模型。
