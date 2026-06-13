# ComfyUI Studio Suite

[English](README.md) | 简体中文

ComfyUI Studio Suite 是一个面向 ComfyUI 的预览版自定义节点包，重点解决提示词编辑、LLM 辅助二次元 tag 工作流、本地 caption 生成、工作流队列辅助，以及图像预处理工具这几条链路。

当前发布状态：`v0.1-preview`

这个仓库现在适合内部使用和外部早期测试。核心工作流已经能用，但整体还没有到“面向所有用户的一键稳定版”阶段。

## 主要能力

- 面向二次元 / Danbooru 体系的提示词与 tag 生成
- 中文转英文 tag、tag 扩写、模型规格化输出
- WD14 + LLM 的训练打标 / caption 辅助
- 基于 `llama_cpp_python_inproc` 的本地 GGUF 推理
- 可选后端适配：KoboldCpp、llama.cpp server、LM Studio、vLLM、OpenAI-compatible API
- 文件夹批处理队列节点
- 图像填充、裁切、缩放辅助节点

## 当前推荐路线

目前测试最充分的路线是：

- 纯文本任务：`Gemma 4 E4B GGUF Q4`
- 视觉任务：`Gemma 4 E4B Vision GGUF` + 对应 `mmproj`
- 后端方式：`llama_cpp_python_inproc`

Qwen 路线现在也可以接，但整体完善度还不如 Gemma 4 E4B。

## 安装

1. 把本仓库放到：

```text
ComfyUI/custom_nodes/comfyui_studio_suite
```

2. 重启 ComfyUI。

3. 复制后端配置模板：

```text
config/backend_profiles.example.json -> config/backend_profiles.json
```

4. 编辑 `config/backend_profiles.json`，把 `model_path` 和 `mmproj_path` 改成你本机的模型路径。

5. 打开示例工作流目录：

```text
examples/workflows
```

6. 在节点包根目录运行自检：

```powershell
python scripts/doctor_release.py
```

## 快速开始

### 1. 先跑纯文本 smoke test

建议先用：

- `examples/workflows/noob_zh_to_en_expand_preview.json`

这个工作流适合先验证：

- 任务模组拼装
- 资源加载
- 本地文本 LLM 推理
- NoobAI 风格提示词格式化

### 2. 再跑视觉 caption 测试

建议再用：

- `examples/workflows/tagging_wd14_llm_anima_train_preview.json`

这个工作流会串接：

- 图片队列输入
- WD14 基础 tag
- 图片路径桥接
- 本地视觉 LLM 修正和补充
- Anima 风格训练 caption 输出

## 示例工作流

- `examples/workflows/noob_zh_to_en_expand_preview.json`
  - 中文描述转英文 Danbooru 风格提示词
  - 面向 NoobAI XL 1.1 输出格式
  - 纯文本工作流

- `examples/workflows/tagging_wd14_llm_anima_train_preview.json`
  - WD14 tag + 本地视觉 LLM 的 caption 修正
  - 面向训练打标工作流
  - 需要视觉 GGUF 和 `mmproj`

详细说明见：

- `examples/workflows/README.md`

## 资源策略

这个仓库默认只追踪轻量资源，不把大资源直接放进 Git。

默认随仓库提供的资源包括：

- `resources/danbooru_character_aliases.json`
- `resources/character_alias_safety.json`
- `resources/task_templates`
- `resources/task_bundles`
- 轻量服装辅助资源

大型可选资源建议单独发布到 Hugging Face Datasets 或 Release 附件，例如：

- 自动生成的大型角色 alias 词典
- 大型 Danbooru 角色表
- tag 统计资源
- tag 共现 CSV
- 画师 wildcard 资源

当前主资源仓库地址：

- `https://huggingface.co/datasets/onglon114514/ComfyUI-Studio-Suite-Resources`

详见：

- `resources/README.md`
- `docs/HUGGINGFACE_RESOURCES.md`

## 大资源下载与放置

如果你只是先测试节点核心功能，仓库自带的轻量资源已经够用。

当你需要下面这些能力时，再去下载大资源包：

- 更完整的 Danbooru 角色 alias 覆盖
- 大型角色表检索
- tag 热度 / 共现辅助工作流
- 画师 wildcard 参考资源
- 更完整的服装查询资源

下载地址：

- `https://huggingface.co/datasets/onglon114514/ComfyUI-Studio-Suite-Resources`

下载后，把文件放到：

```text
ComfyUI/custom_nodes/comfyui_studio_suite/resources
```

文件名不要改。

常见需要放回去的文件包括：

- `danbooru_character_aliases.generated.json`
- `danbooru_character_webui.normalized.jsonl`
- `danbooru_tags_cooccurrence.csv`
- `danbooru_artist_wildcard-D站画师列表.txt`
- `tag_count_tags_统计.jsonl`

只要文件名一致，内置的资源加载器就会自动识别。

## 后端模式

当前支持的执行方向：

- `llama_cpp_python_inproc`
  - 在 ComfyUI Python 进程内直接运行 GGUF
  - 目前最推荐

- `koboldcpp`
  - 管理式本地后端

- `llama_cpp_server`
  - 管理式 llama.cpp server 后端

- `lm_studio`
- `vllm`
- `custom_openai_compat`
  - 连接已经启动好的 OpenAI-compatible 后端

## 仓库结构

- `task_agent_core/`
  - Task Agent 节点与执行逻辑
- `task_agent_gateway.py`
  - 后端适配与任务运行核心
- `queue_nodes.py`
  - 独立队列辅助节点
- `smart_fill_crop_resize_node.py`
  - 图像填充/裁切/缩放辅助节点
- `frontend/`, `web/`
  - 前端资源
- `examples/workflows/`
  - 当前测试过的示例工作流
- `resources/`
  - 轻量内置资源与大资源占位
- `docs/`
  - 安装、后端、迁移、发布说明
- `scripts/`
  - 打包、自检、辅助脚本

## 当前定位

这个预览版更准确的定位是：

- 已经能支撑内部使用和外部早期测试
- 还不是面向所有用户的完全稳定正式版

当前剩余的主要摩擦点包括：

- 模型路径仍然需要手工配置
- 大资源默认不随仓库提供
- Prompt Studio 和一些扩展工作流仍在继续打磨

## 署名与来源

这个仓库整合和重构了多条开发线的功能，说明见：

- `docs/ATTRIBUTION.md`

## 发布前检查

在打包或发布更新前，建议执行：

```powershell
python scripts/doctor_release.py
python scripts/build_release_preview.py
```

它会检查：

- 必需文件是否缺失
- 是否泄露本机路径
- 是否误打包大资源
- 发布结构是否完整
