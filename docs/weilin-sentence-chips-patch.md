# WeiLin 提示词块：句子级分隔补丁

更新时间：2026-11-14

## 为什么

WeiLin 的提示词编辑器（`weilin-comfyui-tools`）沿用 A1111 的 token 模型：每个可拖动的块是一个 token，解析时只按英文逗号/换行切分，拼回时所有 token 一律用 `", "` 连接。这带来两个问题：

- `中文自然语言.中文自然语言` 永远是一块，句号被当成块内正文；
- 即使把解析改成按句号切，保存时也会被 `", "` 重新拼接，句号丢失。

Studio Suite 的翻译与双语对照已经按句子切分，块不切会导致对照文本查不到，所以需要同步修改编辑器的 token 模型。

## 改动位置（本机三方插件，不在本仓库）

`<ComfyUI>/custom_nodes/weilin-comfyui-tools`：

- `src/src/view/prompt_box/prompt_index.vue`
  - `parseNestedBrackets` 返回 `{ text, separator }`，每条分隔符跟随前一个 token；
  - 有句末标点时按 `。！？!?；;` 和受保护的 `.` 切分，逗号留在句内；没有句末标点时保持逗号/分号切分；
  - `updateInputText` / `deleteToken` / `processInput` 统一调用 `joinTokensForInput`，按 token 自带的 separator 重建原文；
  - 新建换行 token 与翻译新增 token 补 `separator` 默认值。
- `dist/javascript/main.entry.js`、`dist/javascript/main.entry.js.map`：用 `src` 重新构建。

备份文件后缀 `.bak-studio-suite-20261114`。

## 重建

该插件没有随包提供 npm。本机用 npmmirror 下载的 npm 11 执行：

```bat
cd <ComfyUI>\custom_nodes\weilin-comfyui-tools\src
node .npm-cli\package\bin\npm-cli.js install --registry=https://registry.npmmirror.com --no-audit --no-fund
node .npm-cli\package\bin\npm-cli.js run build
```

构建输出在插件根目录 `dist/`。改完只需刷新浏览器（ComfyUI 不用重启）。

## 维护注意

- 插件更新会覆盖 `dist` 和 `src`，更新后需要重新应用本补丁；
- 若上游后来支持 separator token 模型，应优先采用上游实现并删除本地补丁；
- 拖动句子块时标点跟随该句移动（例如把第二句拖到第一句前，会得到 `第二句.第一句.`），这是 token 模型的固有行为，不是解析错误。
- 解析器对 `<lora:...>` / `<lyco:...>` / `<wlr:...>` 做整体保护：标签内部的逗号、句点不会被当成块分隔符或句子边界，文件名里带逗号的 LoRA 也能作为一个块拖动。
- 全角括号 `（）` 与半角 `() [] {}` 一样参与嵌套计数，括号内的逗号/句号不会切断整个括号。
- 工具栏新增 `说明` 按钮：弹窗说明卡片切分规则、LoRA 语法和 `+1.1` / `-1.1` 权重写法。
