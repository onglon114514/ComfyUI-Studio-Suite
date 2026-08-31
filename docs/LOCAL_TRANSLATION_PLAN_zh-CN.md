# Prompt Studio 本地翻译与词典路线

## 当前决定

Prompt Studio 暂不增加联网免费翻译接口，也不把通用机器翻译模型设为默认依赖。当前主线是：

```text
角色名 / 作品名 / Danbooru tag -> 本地词典检索与保护
普通自然语言 -> 现有 Gemma 本地 LLM
扩写、规格化、角色校正 -> Task Agent 模组链
```

原因是绘图提示词中的 IP 角色名和 Danbooru canonical tag 不能依赖普通翻译模型猜测。翻译速度也不应通过继续堆叠后端来解决，当前优先改进 Prompt Studio 的交互、状态反馈和模型调度。

## 本地词典

内置小词典：

```text
resources/danbooru_character_aliases.json
```

大型角色资源：

```text
resources/danbooru_character_webui.normalized.jsonl
```

大型 JSONL 不会在每次翻译时整体载入内存。放好资源后，在节点目录运行一次：

```powershell
python scripts/build_local_dictionary_index.py
```

索引生成到：

```text
prompt_studio/storage/cache/danbooru_character_aliases.sqlite3
```

索引是本机缓存，不需要提交 Git。源文件变化后重新运行脚本即可。

## 翻译顺序

1. 在原始文本中检索已知角色别名。
2. 中文角色名先替换为 canonical Danbooru tag，例如 `羽毛笔 -> la_pluma_(arknights)`。
3. 把受到保护的文本交给 LLM 翻译其余自然语言。
4. 返回原文、译文和词典命中关系，供 Prompt Studio 在 tag 下方显示本地语言。

本地词典只解决确定性术语，不承担整句翻译。没有命中词典时仍按原来的 LLM 路线处理。

## Prompt Studio UI

翻译工具栏将方向和内容类型拆开：

```text
方向：中文 -> 英文 / 英文 -> 中文
内容：自然语言整句 / Danbooru Tag 与角色名
```

状态区显示：

```text
基础词典 / 词典条目数
模型未加载 / 加载中 / 已预热
队列空闲 / 队列占用
```

ComfyUI 有运行或等待任务时，翻译按钮禁用并释放翻译 worker。空闲状态降低轮询频率，避免 Prompt Studio 持续高频请求后端。

## 暂缓项

CTranslate2、OPUS-MT、Argos Translate 等专用离线机器翻译路线暂不进入默认安装。它们可以缩短普通句子翻译时间，但并不能替代 Danbooru/IP 词典，而且会增加模型下载、依赖和 UI 选项。等现有界面与 LLM 调度稳定后再单独基准测试。
