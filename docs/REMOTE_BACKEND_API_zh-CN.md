# 远程后端（OpenAI 兼容 API）zh-CN

更新时间：2026-09-20
本次改动：给任务代理后端补上 **API Key 传递**，让"用远程模型做提示词扩写/反推"这条线真正可用。

## 1. 之前缺什么

网关里跟 HTTP 有关的只有三个函数：

| 位置 | 函数 | 原来带的头 |
| --- | --- | --- |
| `task_agent_gateway.py` | `http_get_json` | 无 |
| `task_agent_gateway.py` | `http_post_json` | 只有 `Content-Type` |
| `task_agent_gateway.py` | `http_post_openai_stream` | 只有 `Content-Type` / `Accept` |

`_complete_messages()` 调它们时**不带任何鉴权头**，所以凡是需要认证的云端 OpenAI 兼容服务（DeepSeek、Qwen、OpenRouter 等）都调不通 —— 全仓库搜 `api_key` / `Authorization` 当时是零命中。

## 2. 现在怎么用

后端配置里加一个字段，**只写环境变量名**：

```json
{
  "backend": {
    "provider": "custom_openai_compat",
    "base_url": "https://api.deepseek.com",
    "api_key_env": "DEEPSEEK_API_KEY"
  }
}
```

节点启动时从进程环境变量读 `DEEPSEEK_API_KEY`，有值就带上 `Authorization: Bearer <值>`；没设置就**照常不带**（本地后端不受影响）。

可用的示例配置：

| 文件 | 用途 |
| --- | --- |
| `config/task_agent_config.example.json` | 本地 llama-cpp-python（新增 `api_key_env: ""`） |
| `config/task_agent_config.llama_cpp_server.example.json` | 托管 llama.cpp server（新增 `api_key_env: ""`） |
| `config/task_agent_config.openai_compat.example.json` | **远程 OpenAI 兼容服务**（本机新增） |

支持带鉴权的 provider：`custom_openai_compat`、`lm_studio`、`vllm`、`llama_cpp_server`。

## 3. 安全约定

- **密钥不进仓库**：配置里只有环境变量名，密钥本身放在系统环境变量或受保护凭据存储里。
- **`api_key` 这种直接写值的字段会被忽略**：`backend_auth_headers()` 只认 `api_key_env`。有测试专门守这条（`test_key_is_never_read_from_config_values`），防止有人图省事把密钥写进配置。
- `*.local.json` 本来就已 gitignore，本机配置不会被提交。

## 4. 测试

```powershell
& "E:\AI\ComfyUI-aki-v2\python\python.exe" -B scripts/test_backend_api_key.py
```

覆盖 9 项：无配置 / 空变量名 / 变量未设置 / 变量为空串 / 变量有值 → 正确生成 Bearer；以及三个 HTTP 函数**真的把 Authorization 发到线上**（用本地假服务器收头验证），同时保证 SSE 的 `Accept` 头没被破坏、无密钥时不发该头。

## 5. 未验证的部分

- **真实云端 API 的端到端调用**：未验证。需要真实 key + 网络，而且会消耗额度，所以留给用户手工确认。
- 各家服务的鉴权差异（例如有的用 `x-api-key`、有的要额外 header）：目前只实现了标准 `Authorization: Bearer`。如果某个服务不是这套，需要再扩 `api_key_env` 之外的配置。

## 6. 和"本地模型代理"的关系

两条路**共用同一套后端选择逻辑**，但失败模式完全不同：

- 本地后端：模型加载、显存、架构支持
- 远程后端：连接、凭据、额度

调试时先确认 `provider` 是不是 OpenAI 兼容那一档，再确认环境变量到底有没有值。
