# 本地 OpenAI 兼容端点（给外部 agent 用）zh-CN

更新时间：2026-09-20
本次改动：节点在 ComfyUI 自带的 aiohttp 上新增一组 **OpenAI 兼容端点**，让外部 agent（OpenClaw / Codex / Claude Code 等）把本机模型当模型源用 —— 会话里传张图（base64）就能做反推/打标，**任何一侧都不需要配 API key**。

> 反向能力（节点作为**客户端**调远程 OpenAI 兼容 API）见 `docs/REMOTE_BACKEND_API_zh-CN.md`。两者共用同一套 `ManagedBackend`，但鉴权模型完全相反：远程方向靠 `api_key_env` 读环境变量，本地这个端点靠**回环限制**而不是密钥。

## 1. 默认是关的，怎么开

端点**默认关闭**，且只接受回环连接。开启方式（二选一）：

1. 复制 `config/local_openai_server.example.json` 为 `config/local_openai_server.local.json`（已被 gitignore），把 `enabled` 改成 `true`。
2. 或设环境变量 `STUDIO_SUITE_LOCAL_OPENAI_ENABLED=1`（`0/off/false/no` 可强制覆盖配置为关）。

开关是**逐请求生效**的，改完配置不用重启 ComfyUI。

配置项（都有默认值，example 文件里是全量）：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 总开关 |
| `max_request_bytes` | 32 MiB | 整个请求体上限，超出 → 413 |
| `max_image_bytes` | 10 MiB | 单张图片解码后上限，超出 → 413 |
| `max_images_per_request` | 4 | 单请求图片张数上限 |
| `max_messages` | 64 | 消息条数上限 |
| `request_timeout_sec` | 600 | 单次补全硬超时，超时 → 504 |
| `allow_remote_image_url` | `false` | 是否允许 http(s) 图片 URL（见 §4） |
| `default_backend_profile` | `""` | 默认档案键名，空 = 自动选择 |
| `queue_guard` | `true` | 出图队列忙时拒绝 LLM 请求（409） |

## 2. 端点与 base URL

两套路径同时注册（handler 相同）：

| 用途 | 命名空间路径（永远可用） | 裸路径（未被别的插件占用时） |
| --- | --- | --- |
| 模型列表 | `GET /studio-suite/llm/v1/models` | `GET /v1/models` |
| 对话补全 | `POST /studio-suite/llm/v1/chat/completions` | `POST /v1/chat/completions` |

外部 agent 的 base URL 填（假设 ComfyUI 端口 8188）：

```
http://127.0.0.1:8188/studio-suite/llm/v1
```

裸 `/v1` 可用时填 `http://127.0.0.1:8188/v1` 也行。节点启动日志会打印 `root_v1=yes/no`。**建议一律用命名空间路径**，裸路径只是便利，别的插件先占了就自动降级。

## 3. 怎么被调用

### OpenClaw / Codex / 任意 OpenAI 客户端

任何支持自定义 base URL 的 OpenAI 兼容客户端都直接可用。API key 随便填或不填（服务端不校验，回环即信任）。

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8188/studio-suite/llm/v1", api_key="local")

# 文本
print(client.chat.completions.create(
    model="gemma4_e2b_hauhau_q8",  # 或省略/写 auto
    messages=[{"role": "user", "content": "用一句话介绍 Danbooru"}],
).choices[0].message.content)

# 传图反推（base64 data URL，必须）
import base64
data_url = "data:image/png;base64," + base64.b64encode(open("a.png", "rb").read()).decode()
resp = client.chat.completions.create(
    model="auto",  # 带图时自动选 vision 档案
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "Output Danbooru tags for this image, comma separated."},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]}],
    stream=True,  # 流式 SSE 完全支持
)
for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="")
```

「要标签还是要描述」由你在 `messages` 里写明（参考模板 `resources/task_templates/vision_tagging_qwen.json` 与 `image_caption_qwen.json` 的语义：前者是纯 Danbooru 标签行，后者是自然语言段落）。这个端点只做**透传**，不套任务模板。

### 模型选择规则

- `model` 填档案键名（`GET /v1/models` 里的 `id`），或档案里的 `model_name` 别名。
- `model` 省略 / `auto` / `default`：无图 → `default_backend_profile` 或第一个档案；**带图 → 自动选视觉档案**（优先键名含 `vision` 的，其次 `supports_vision: true` 或配了 `mmproj_path` 的）。
- 带图却指定了非视觉档案 → 400 `model_not_vision_capable`；未知模型 → 404 `model_not_found`。

## 4. 限制（并发 / 超时 / 回环 / 体积）

| 限制 | 行为 | 返回 |
| --- | --- | --- |
| 未启用 | 两个端点都拒绝 | 403 `server_disabled` |
| 非回环来源 | 即使 ComfyUI 绑了 0.0.0.0 也拒绝 | 403 `loopback_only` |
| 并发 | **单飞**：同时只跑一个 LLM 请求 | 429 `server_busy` |
| 超时 | 超过 `request_timeout_sec` | 504 `completion_timeout`（后台请求会继续跑完才释放槽位） |
| 出图队列忙 | ComfyUI 队列有任务时拒绝启动 | 409 `comfy_queue_busy` |
| 出图任务到达 | 队列接受工作流**之前**会主动卸载已加载的 LLM 后端（与 HY-MT 同一钩子），长 LLM 请求不会挡出图 | 进行中的请求会以 SSE 错误块 / 502 结束 |
| 请求体 / 图片过大 | 见 §1 上限 | 413 |
| 远程图片 URL | 默认拒绝，节点绝不替客户端拉取任意网络资源；确需时设 `allow_remote_image_url: true` | 400 `remote_image_url_not_allowed` |

错误一律是标准 OpenAI 结构：

```json
{"error": {"message": "...", "type": "invalid_request_error", "param": null, "code": "..."}}
```

流式中途出错时，错误也作为 `data: {"error": ...}` 块下发，然后照常 `data: [DONE]`。

其他注意：

- `usage` 的 token 数是**估算值**（按字符数粗算），节点不为此跑分词器。
- 转发参数白名单：`temperature`、`max_tokens`（含 `max_completion_tokens`）、`top_p`、`stop`、`seed`、`frequency_penalty`、`presence_penalty`；其余 OpenAI 参数静默忽略。
- 端点与 Prompt Studio 翻译、任务代理节点**共享同一个 `ManagedBackend`**：同一份已加载模型、同一个生命周期，不会并存两份权重。
- in-process provider 下，带图请求要求所选档案能解析出 `mmproj_path`，否则后端会以 502 报错（选键名含 `vision` 的档案即可）。

## 5. 测试

```powershell
& "<ComfyUI python>\python.exe" -B scripts/test_local_openai_server.py
```

24 项，全部用**注入的假后端**（真 aiohttp 起在 127.0.0.1 随机端口，不加载任何模型）：

- `/v1/models` 形状 + 视觉标记（含裸路径与命名空间路径）
- 非流式 chat 的 OpenAI 响应形状、转发 payload 内容
- base64 `image_url` 被解析并自动选中 vision 档案
- 远程 `image_url` 被拒（400）、非 base64 / 非 image/* / 超大图片被拒
- `stream: true` 的 SSE 分片（role 块 → 内容块 → finish_reason=stop → `[DONE]`）
- 超大请求体 413、非法 JSON 400、未知模型 404
- 未启用 403、队列忙 409、单飞占用中 429
- 纯函数：回环判定（含 `::1`、IPv4-mapped）、vision 判据、配置加载与环境变量覆盖

回归脚本 `scripts/test_backend_api_key.py`（9 项）不受影响。

## 6. 未验证的部分

- **真接一次外部 agent**（OpenClaw/Codex 以本端点为模型源跑通一次反推）：需要重启 ComfyUI、开启配置、本机有 vision 模型权重，留给用户手工验证。
- 裸 `/v1` 与其他插件路径冲突时的降级：代码里已预查 RouteTableDef 并跳过，但只在没有冲突插件的本机验证过「可注册」分支。
- 超时 504 后后台请求自然结束的槽位释放：逻辑上由 worker 的 done-callback 释放锁，未用真实长请求压测。
