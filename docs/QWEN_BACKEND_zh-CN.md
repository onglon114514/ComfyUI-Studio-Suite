# Qwen 反推与视觉打标路线（zh-CN）

更新时间：2026-09-20
本次新增：Qwen 系列后端档案、视觉反推任务模板、视觉任务链，以及这套配置的自检脚本。

## 1. 新增了什么

| 文件 | 作用 |
| --- | --- |
| `config/backend_profiles.example.json` | 新增 4 个 Qwen 档案：2 个视觉（带 mmproj）、2 个纯文本 |
| `config/model_downloads.json` | 对应 4 个模型的下载条目（全部 `required: false`，不下载不会让 doctor 报错） |
| `resources/task_templates/vision_tagging_qwen.json` | 视觉反推标签（`vision_tagging`，输入 `image_path`） |
| `resources/task_templates/image_caption_qwen.json` | 视觉生成自然语言描述（`image_captioning`，输入 `image_path`） |
| `resources/task_bundles/chain_qwen_vision_to_caption.json` | 视觉任务链：视觉打标 → 自然语言 caption |
| `scripts/test_qwen_profiles.py` | 上述配置的一致性自检 |

档案键（profile_key）命名规则：**视觉档案的键必须含 `vision`**（`backend_choice_supports_vision()` 依赖这一点判断能否喂图）。

## 2. 8GB 显存（4060 级）推荐组合

| 场景 | 视觉档案 | 文本档案 | 说明 |
| --- | --- | --- | --- |
| 反推 + 打标（首选） | `qwen3_vl_4b_vision_q4` | `qwen3_4b_q4` | 与 ComfyUI 同机跑图时，视觉模型只用于偶发反推，显存压力最小 |
| 反推质量优先 | `qwen3_vl_8b_vision_q4` | `qwen3_8b_q4` | 需要先空出显存；建议配合 `--disable-dynamic-vram` 与较低的 GPU 层数 |
| 纯 CPU 兜底 | `qwen3_vl_4b_vision_q4`（`n_gpu_layers=0`） | `qwen3_4b_q4` | 最慢但最稳，适合无人值守批量 |

`config/task_agent_config.example.json` 里的 `llama_cpp_python_n_gpu_layers: 0`、`llama_cpp_python_n_batch: 512` 是本机稳定基线的取值，**不要为了跑 Qwen 直接改成全量 GPU 卸载**；先按稳定基线跑通，再单独调。

## 3. 本地路线怎么用

1. 把模型放到档案里写的相对路径（或按 `config/model_downloads.json` 的 `target` 放置）；
2. 后端 provider 用 `llama_cpp_python_inproc`，`default_profile` 选上表里的档案；
3. 视觉任务用 `resources/task_templates/vision_tagging_qwen.json` 或 `image_caption_qwen.json`；
4. 想要"打标 + 描述"两段式，用 `resources/task_bundles/chain_qwen_vision_to_caption.json`；
5. 跑完记得用清理节点卸载模型。

**不要**把视觉任务当成 8GB 机器的默认基线：`docs/TAGGING_STABLE_BASELINE.md` 里的 WD14 + 文本模型路线仍然是默认推荐，视觉路线保持可选。

## 4. API（OpenAI 兼容）路线：能用的部分与缺口

现状（本次读代码确认）：

- 网关已经支持 OpenAI 兼容 provider：`lm_studio`、`vllm`、`custom_openai_compat`（另有 `llama_cpp_server`）。
  这些 provider 的请求地址是 `base_url` 归一化后的 `<base>/v1/chat/completions`，健康检查是 `<base>/v1/models`。
- **但网关目前没有任何 API Key 传递逻辑**：`task_agent_gateway.py`、`config/*.json` 里搜不到 `api_key` / `Authorization` / `Bearer` 的处理（只有 `prompt_studio/i18n.json` 里的界面文案提到了 API Key）。

结论：**要真正跑通 DeepSeek / Qwen 这类需要鉴权的云端 API，还差"鉴权头"这一步。** 这是下一步要补的最小改动，建议做法：

1. 后端配置加 `api_key_env`（只存**环境变量名**，不存密钥本身）；
2. 发送请求时，若该环境变量存在，就带 `Authorization: Bearer <值>`；
3. 配置示例里只写 `api_key_env`，密钥留在系统环境变量里，**不进仓库**。

在补完之前，`custom_openai_compat` 只能对接**不需要鉴权**的本地 OpenAI 兼容服务（例如本机其它推理服务）。

## 5. 必须核对（本次无法联网核对）

调研时段本机无法访问 `huggingface.co`（连接超时），因此下列内容**未经上游核实**，下载前请自行确认：

- `Qwen/Qwen3-VL-8B-Instruct-GGUF`、`Qwen/Qwen3-VL-4B-Instruct-GGUF`、`Qwen/Qwen3-8B-GGUF`、`Qwen/Qwen3-4B-GGUF` 这四个仓库 id；
- 对应的量化文件名与 mmproj 文件名；
- 目标机器上的 `llama-cpp-python` / `llama-server` 版本是否已经支持 Qwen3-VL 的视觉塔。

如果仓库 id 或文件名与实际不一致，只改 `config/model_downloads.json` 与 `config/backend_profiles.example.json` 中的对应字段即可，不需要动 Python 代码。

## 6. 未验证项

- 视觉反推在 8GB 显存上的真实耗时、显存峰值、批量稳定性：**未验证**（本机没下载模型，也没有跑 GPU 测试）。
- 视觉模型输出是否稳定落在 Danbooru 标签风格：**未验证**，模板里的 `style_hint` 是初始值，需要按实际输出微调。
- 视觉打标与 WD14 结果的一致性对比：**未验证**。
