# Studio Suite 网页面板启动器

给不写代码的使用者：双击 `start-studio-suite.bat`，启动 7860 面板并自动打开浏览器。

## 前提

- ComfyUI 已安装，节点包放在 `<ComfyUI>/custom_nodes/` 下。
- 先启动 ComfyUI（秋叶启动器）。本启动器**只启动面板**，不启动 ComfyUI。
- 机器上有 Python 3（启动器本身只用标准库；网关会优先使用 ComfyUI 自带的 Python）。

## 用法

1. 双击 `start-studio-suite.bat`。
2. 控制台显示面板地址，浏览器自动打开 `http://127.0.0.1:7860/`。
3. 关闭黑窗口或按 Ctrl+C 停止面板。

只检查不启动：

```bat
python launcher\studio_suite_launcher.py --check
```

临时换端口：

```bat
python launcher\studio_suite_launcher.py --port 7870
```

## 可选配置

在 `launcher/config.local.json`（已被 `.gitignore` 排除，不会入库）里覆盖：

```json
{
  "host": "127.0.0.1",
  "port": 7860,
  "pythonPath": "",
  "openBrowser": true,
  "checkComfyUi": true
}
```

- `pythonPath` 留空时自动找 `<ComfyUI>/../python_embeded`、`python`、`python313`。
- 如果启动失败，优先把 `pythonPath` 指向 ComfyUI 自带的 `python.exe`。
- 端口冲突时改 `port`；面板已经在跑时，启动器会直接打开浏览器，不会重复起进程。
