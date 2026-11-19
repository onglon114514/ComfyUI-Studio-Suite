# Prompt Studio 切分模式（Tag / 自然语言）

更新时间：2026-11-16

## 背景

Prompt Studio 的内嵌编辑器使用 `prompt_studio/bundle/main.entry.js`（physton `sd-webui-prompt-all-in-one-app` 的构建产物，仓库内没有对应源码）。原解析器 `lk` 会把 `。` 等统一替换成逗号，再按逗号和空格切卡片，因此：

- 自然语言里的句号不会切卡片；
- 方括号 `[...]` 整段被当作一个 token，整段描述会糊成一块；
- 标签模式和自然语言模式共用同一套规则，互相打架。

## 改动

`main.entry.js` 内新增三个函数（位于 `const lk=` 之前）：

- `ssSplitMode()`：从 `localStorage["studio_suite_prompt_split_mode"]` 读取 `tag`（默认）或 `nl`；
- `ssNlSplit(text)`：自然语言切分器；
- `ssSplitTags(text, ...)`：`tag` 模式调用原 `lk`，`nl` 模式调用 `ssNlSplit`。`splitTags` 已改为调用它。

`genPrompt` 在拼接前增加一行：当模式为 `nl` 且 token 以 `. 。 ! ? ！ ？ , ， ; ；` 结尾时，用空格（最后一个用空串）代替默认的 `", "`，保证标点保留在 prompt 里。

`prompt_studio/frontend/index.html`：

- 右下角新增「切分：Tag / 自然语言」切换条和「说明」弹窗；
- 模式按提示词框独立保存：写在该 textarea 的 `data-ss-split-mode` 属性和 `localStorage["studio_suite_prompt_split_mode:<id>"]` 上；切换只重新解析当前框，正向/负向互不影响；
- bundle 缓存键改为 `?v=studio-suite-20261116-splitmode`。

## 两套规则

Tag 模式（默认）：

- 逗号、换行切卡片；
- `()` `[]` `{}` `（）` 内不切；
- `<lora:...>` / `<lyco:...>` / `<wlr:...>` 整体保护。

自然语言模式：

- 逗号后接空格才切（`dark,ornate` 不切）；
- `.` `!` `?` `;` `。` `！` `？` `；` 直接切，不需要空格；
- `、` 不切；
- `1.5`、`.safetensors` 等后缀、`...`、`<lora:...>`、括号内不切；
- 标点保留在 prompt 中。

注意：Tag 模式下编辑过的文本会被 `genPrompt` 用逗号重排，原来的句号可能已经丢失；要在自然语言模式下**先切换再粘贴**。

## 回退

备份：`prompt_studio/bundle/main.entry.js.bak-studio-suite-20261116`（本机，未入库）。恢复时覆盖回 `main.entry.js` 即可。
