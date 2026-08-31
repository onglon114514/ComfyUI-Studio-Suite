# ComfyUI Studio Suite 最小可用配置

这份文档只讲第一次安装后最容易跑通的路线。复杂后端、视觉模型、26B 模型、API 后端都先不要碰。

需要查完整节点用途、连线和字段说明时，请看 `docs/NODE_USER_GUIDE_zh-CN.md`。

## 1. 推荐先用哪个节点

第一次使用只需要这几个：

- `Prompt Studio 正向输出`：写提示词或 WD14 tag。
- `任务代理·任务模组拼装器`：选择要做什么任务。
- `任务代理·资源加载器`：可选，加载 Danbooru alias 等参考资源。
- `任务代理·上下文拼装`：把任务、资源、额外文本合成一个上下文包。
- `任务代理·本地LLM文本工具`：最简单的本地 GGUF 文本执行节点。

如果你已经有 LM Studio、vLLM、KoboldCpp 这种外部后端，才使用 `任务代理·标签工具`。

## 1.1 Prompt Studio 常用功能

Prompt Studio 是多数用户最常用的入口，主要用于：

- 写正向 / 反向提示词。
- 从分组 tag 面板点选 tag。
- 管理自定义 group tags。
- 读取 LoRA / embedding 列表和 LoRA 信息。
- 使用 `LLM翻译正向` / `LLM翻译反向` 做中文自然语言和英文提示词的双语翻译。

Prompt Studio 的用户数据在：

```text
prompt_studio/storage
```

其中常用目录：

- `prompt_studio/storage/group_tags`：分组 tag。
- `prompt_studio/storage/autocomplete`：自动补全词。
- `prompt_studio/storage/local_complete_tags`：本地补全 CSV。
- `prompt_studio/storage/prompt_data`：历史、收藏、界面设置。

LLM 翻译按钮默认使用节点私有 llama.cpp worker 和最小 E2B GGUF 模型。首次使用前需要安装一个本地 GGUF 模型；如果只是想写提示词、点 tag、查 LoRA，不配置 LLM 也能使用 Prompt Studio。

## 2. 最小模型配置

Prompt Studio 翻译和轻量文本任务推荐先安装：

- `Gemma 4 E2B Hauhau Q8 GGUF`

自动安装：

```powershell
python scripts/install_min_llm_model.py
```

如果网络失败，打印手动下载地址和放置路径：

```powershell
python scripts/install_min_llm_model.py --manual
```

默认放置路径：

```text
models/llm/gemma-4-e2b-hauhau-q8/Gemma-4-E2B-Uncensored-HauhauCS-Aggressive-Q8_K_P.gguf
```

需要更高质量的打标 / 规格化时，再准备：

- `Gemma 4 E4B GGUF Q4`

然后复制配置：

```text
config/backend_profiles.example.json -> config/backend_profiles.json
```

编辑 `config/backend_profiles.json`：

```json
{
  "gemma4_e2b_hauhau_q8": {
    "model_path": "D:/your/model/path/model.gguf"
  }
}
```

只测试纯文本时不需要 `mmproj_path`。

## 3. 首次测试参数

在 `任务代理·本地LLM文本工具` 里建议先用：

- `model_source = profile_catalog`
- `backend_profile = gemma4_e2b_hauhau_q8 | Gemma 4 E2B Hauhau Q8 Text`
- `context_size = 1024`
- `llama_cpp_python_n_gpu_layers = 999`
- `llama_cpp_python_n_batch = 256`
- `unload_after_run = true`

如果要批量打标，并且后面不接生图采样器：

- `unload_after_run = false`
- 队列最后接 `任务代理·结束清理`

## 4. 训练打标稳定基线

低显存用户建议先跑：

```text
examples/workflows/tagging_wd14_llm_anima_train_preview.json
```

目标输出格式：

```text
wd14, original, tags, kept, exactly

Natural-language caption generated from those WD14 tags.
```

这条基线要求：

- WD14 tag 第一行完全保留。
- LLM 只补第二段自然语言。
- 不使用视觉模型。
- 不使用 `refine_wd14_tags`。
- 不添加 `masterpiece`、`best quality`、`score_` 这类绘图质量词。

## 5. 常见报错

### 找不到 `utils.install_util`

说明 ComfyUI 根目录没有正确进入 Python import 路径，或者使用的是旧版本发布包。更新后运行：

```powershell
python scripts/doctor_release.py
```

如果 `errors` 为 `0`，这个问题已经通过运行时路径修复。

### `llama_cpp was already imported from another location`

说明别的节点先导入了全局 `llama_cpp`。解决方式：

```powershell
python scripts/install_llama_preload_shim.py
```

然后重启 ComfyUI。

### 输出为空或任务没执行

优先检查：

- `context_bundle_json` 是否真的连到了执行节点。
- `任务模组拼装器` 是否至少有一个任务。
- `backend_profiles.json` 里的 `model_path` 是否真实存在。
- `context_size` 是否小于输入 token 数。

### 显存爆掉

纯文本批量打标时先用：

- `llama_cpp_python_n_gpu_layers = 0`
- `llama_cpp_python_n_batch = 128`

如果同一工作流后面要立刻生图，必须在采样前卸载 LLM，或者使用 `任务代理·结束清理`。

### Prompt Studio 打开后空白或按钮缺失

优先检查：

- 浏览器强制刷新 ComfyUI 页面。
- 运行 `python scripts/doctor_release.py`，确认 `prompt_studio/frontend/index.html`、`prompt_studio/bundle/main.entry.js`、`prompt_studio/frontend/js/studioSuiteLlmTools.js` 都存在。
- 如果从 GitHub 下载 zip，确认目录没有多套旧节点同时启用。
- 如果旧版 `weilin-comfyui-prompt-all-in-one` 还在 `custom_nodes` 中启用，先禁用，只保留 Studio Suite。

当前版本已经移除 Prompt Studio 页面里的 Google Fonts 外链，避免国内网络导致 iframe 打开后延迟或空白。
