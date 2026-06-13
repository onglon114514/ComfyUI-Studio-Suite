# GitHub Release Draft: v0.1-preview

You can use the following content as the first GitHub release body.

---

## ComfyUI Studio Suite v0.1-preview

This is the first preview release of **ComfyUI Studio Suite**, a custom-node bundle for:

- prompt editing
- LLM-assisted anime / Danbooru tag workflows
- local caption generation
- image/text queue helpers
- fill / crop / resize utilities

## Included In This Preview

- Task Agent core nodes
- local `llama_cpp_python_inproc` execution path
- backend adapters for KoboldCpp / llama.cpp server / LM Studio / vLLM / OpenAI-compatible APIs
- example workflows for:
  - Chinese-to-English NoobAI-style prompt generation
  - WD14 + vision LLM caption generation for training workflows
- lightweight bundled resources
- release self-check and packaging scripts

## Recommended Current Setup

Most tested path:

- text tasks: `Gemma 4 E4B GGUF Q4`
- vision tasks: `Gemma 4 E4B Vision GGUF` + matching `mmproj`
- backend provider: `llama_cpp_python_inproc`

## Large Optional Resources

Large resource files are hosted separately on Hugging Face:

- https://huggingface.co/datasets/onglon114514/ComfyUI-Studio-Suite-Resources

These resources include:

- generated character alias dictionaries
- large character source tables
- tag-count statistics
- tag co-occurrence CSVs
- artist wildcard references
- clothing lookup resources

After downloading, place them into:

```text
ComfyUI/custom_nodes/comfyui_studio_suite/resources
```

## Installation

1. Put this repository under:

```text
ComfyUI/custom_nodes/comfyui_studio_suite
```

2. Restart ComfyUI.
3. Copy:

```text
config/backend_profiles.example.json -> config/backend_profiles.json
```

4. Edit model paths in `config/backend_profiles.json`.
5. Start with the example workflows in `examples/workflows`.

## Known Limitations

- This is still a preview release, not a final stable release.
- Model paths still require manual configuration.
- Large resources are intentionally not bundled with the code repository.
- Some Prompt Studio and extended workflows are still under active refinement.

## Notes

- Main repository: https://github.com/onglon114514/ComfyUI-Studio-Suite
- Chinese README: `README.zh-CN.md`
- Resource hosting guide: `docs/HUGGINGFACE_RESOURCES.md`

---

## 中文发布说明

这是 **ComfyUI Studio Suite** 的首个预览版发布。

当前这个版本重点提供：

- 提示词编辑
- LLM 辅助二次元 / Danbooru tag 工作流
- 本地 caption 生成
- 图像/文本队列辅助
- 图像填充、裁切、缩放工具

### 本次预览版包含

- Task Agent 核心节点
- 本地 `llama_cpp_python_inproc` 推理路径
- KoboldCpp / llama.cpp server / LM Studio / vLLM / OpenAI-compatible API 的后端适配
- 两套示例工作流：
  - 中文转英文 NoobAI 风格提示词
  - WD14 + 视觉 LLM 的训练打标 / caption
- 轻量内置资源
- 自检脚本与打包脚本

### 当前推荐路线

目前最稳的测试路线：

- 纯文本任务：`Gemma 4 E4B GGUF Q4`
- 视觉任务：`Gemma 4 E4B Vision GGUF` + 对应 `mmproj`
- 后端方式：`llama_cpp_python_inproc`

### 大资源下载

大资源单独托管在 Hugging Face：

- https://huggingface.co/datasets/onglon114514/ComfyUI-Studio-Suite-Resources

下载后放到：

```text
ComfyUI/custom_nodes/comfyui_studio_suite/resources
```

### 当前限制

- 这是预览版，不是完全稳定版
- 模型路径仍需手工配置
- 大资源不会随代码仓库一起提供
- Prompt Studio 和部分扩展工作流仍在继续打磨
