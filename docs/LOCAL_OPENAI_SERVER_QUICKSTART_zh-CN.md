# 三步接入：让别的 agent 用上 ComfyUI 里的本地模型

一句话：这个端点把 ComfyUI 里那套本地模型，变成任何 **OpenAI 兼容客户端** 都能直接调用的模型源 —— 两边都不用配密钥。

它默认是**关的**，只认**本机**连接。这是故意的（理由在最后一节）。

---

## 第 1 步：打开它

1. 打开项目里的 `config/` 目录
2. 把 `local_openai_server.example.json` **复制**一份，改名成 `local_openai_server.local.json`
3. 把里面的 `"enabled": false` 改成 `"enabled": true`
4. **重启 ComfyUI**

说明：

- `local_openai_server.local.json` 不会被提交进 git，放心改。
- 不改这个文件的话，端点一定返回 `403 server_disabled`。
- 嫌麻烦也可以不建文件，改成启动 ComfyUI 前设一个环境变量：`STUDIO_SUITE_LOCAL_OPENAI_ENABLED=1`（它的优先级比配置文件高）。

## 第 2 步：把地址填进客户端

| 要填的 | 值 |
| --- | --- |
| Base URL | `http://127.0.0.1:8188/studio-suite/llm/v1` |
| API Key | 随便一个非空字符串，例如 `local` |

- 地址**结尾要带 `/v1`**。
- 本端点**不校验密钥**，它只检查连接是不是来自本机。所以 key 填什么都行，但不能空着（很多客户端不允许空）。
- 裸路径 `http://127.0.0.1:8188/v1` 有时也可用；但如果被别的插件先注册了，只有带命名空间的那条能用 —— ComfyUI 启动日志里会打印提示。

**自测**：浏览器直接打开 `http://127.0.0.1:8188/studio-suite/llm/v1/models`，能看到一串模型就说明通了。

## 第 3 步：在 OpenClaw 里接上

```json5
{
  models: {
    // merge = 云端模型继续可用，本地这条只是多一个选择
    mode: "merge",
    providers: {
      comfyui: {
        baseUrl: "http://127.0.0.1:8188/studio-suite/llm/v1",
        apiKey: "local",              // 本地占位值，不是真密钥
        api: "openai-completions",
        models: [
          {
            id: "<从 /v1/models 里挑一个>",
            name: "ComfyUI 本地模型",
            reasoning: false,
            input: ["text"],           // 视觉档案改成 ["text", "image"]
          },
        ],
      },
    },
  },
}
```

- `id` 用 `/v1/models` 返回的那串；不确定就先填 `auto`。
- 临时切换：在会话里发 `/model comfyui/<id>`。
- 想看图片（视觉档案）：`input` 写成 `["text", "image"]`。`image_url` **只接受 base64 data URL**，远程图片链接默认被拒。

---

## 常见报错对照表

| 你看到的 | 它的意思 | 怎么办 |
| --- | --- | --- |
| `403 server_disabled` | 端点没打开 | 回第 1 步 |
| `403 loopback_only` | 不是从本机访问的 | 只允许本机。远程请用 SSH 隧道，不要暴露公网 |
| `409 comfy_queue_busy` | ComfyUI 正在出图或有任务排队 | 等它跑完。**这是故意设计的**：绝不让它抢出图要用的显存 |
| `429 server_busy` | 上一个请求还没结束 | 稍等再发。单飞设计，一次只跑一个 |
| `413 request_too_large` | 请求体或图片太大 | 压缩图片 / 拆小请求 |
| `400 invalid_json` | 请求体不是合法 JSON | 检查客户端拼的 body |
| `401` | **本端点不会返回 401** | 说明你打到了别的服务：核对端口和路径 |
| 连不上 / 端口被占 | ComfyUI 没启动，或端口改过 | 先启动它；用过 `--port` 就跟着改地址 |
| 第一次很慢 | 首次请求要加载模型 | 看 ComfyUI 控制台，加载完就快了 |

## 为什么可以放心开着

- **默认关闭** —— 不显式打开就一律 403。
- **只认回环** —— 即使 ComfyUI 监听 `0.0.0.0`，它也逐个请求检查来源地址。
- **单飞 + 超时** —— 长请求不会堆积。
- **出图优先** —— ComfyUI 接受工作流前，会把共享的本地模型后端卸掉，所以不会和出图抢显存。
- **远程图片默认拒绝** —— 需要时再显式打开。
