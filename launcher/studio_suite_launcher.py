#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Studio Suite 网页面板启动器（stdlib only）。

双击 start-studio-suite.bat 或运行：
    python launcher/studio_suite_launcher.py

只启动 7860 的 Studio Suite 前端网关，不启动 ComfyUI 本身。
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE_DIR = HERE.parent
GATEWAY = PACKAGE_DIR / "task_agent_gateway.py"
CONFIG_PATH = HERE / "config.local.json"

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 7860,
    "pythonPath": "",
    "openBrowser": True,
    "checkComfyUi": True,
}


def load_config() -> dict:
    config = dict(DEFAULTS)
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                config.update(data)
        except Exception as error:  # noqa: BLE001
            print(f"[launcher] config.local.json 解析失败，使用默认值：{error}")
    return config


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 400
    except Exception:  # noqa: BLE001
        return False


def find_python(config: dict) -> str:
    configured = str(config.get("pythonPath") or "").strip()
    if configured and Path(configured).is_file():
        return configured

    # <ComfyUI>/custom_nodes/<package> -> <ComfyUI>
    comfy_root = PACKAGE_DIR.parents[1] if len(PACKAGE_DIR.parents) >= 2 else PACKAGE_DIR.parent
    install_root = comfy_root.parent
    candidates = [
        install_root / "python_embeded" / "python.exe",
        install_root / "python" / "python.exe",
        install_root / "python313" / "python.exe",
        comfy_root / "python_embeded" / "python.exe",
        comfy_root / "python.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            return found
    return sys.executable


def build_gateway_config(host: str, port: int) -> Path | None:
    """Write a launcher-local gateway config so --port/--host actually apply."""
    for name in ("task_agent_config.local.json", "task_agent_config.example.json"):
        template = PACKAGE_DIR / "config" / name
        if template.is_file():
            break
    else:
        return None
    try:
        data = json.loads(template.read_text(encoding="utf-8-sig"))
    except Exception as error:  # noqa: BLE001
        print(f"[launcher] 网关配置模板解析失败，按默认启动：{error}")
        return None
    if not isinstance(data, dict):
        return None
    gateway = data.setdefault("gateway", {})
    gateway["host"] = host
    gateway["port"] = port
    target = HERE / "gateway_config.local.json"
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception as error:  # noqa: BLE001
        print(f"[launcher] 无法自动打开浏览器：{error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Studio Suite 网页面板启动器")
    parser.add_argument("--check", action="store_true", help="只检查路径/端口/ComfyUI，不启动")
    parser.add_argument("--host", default=None, help="临时覆盖监听地址")
    parser.add_argument("--port", type=int, default=None, help="临时覆盖监听端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    config = load_config()
    host = str(args.host or config.get("host") or "127.0.0.1").strip()
    port = int(args.port or config.get("port") or 7860)
    url = f"http://{host}:{port}/"
    python = find_python(config)

    print("=== Studio Suite 网页面板启动器 ===")
    print(f"[launcher] 面板地址 : {url}")
    print(f"[launcher] 网关脚本 : {GATEWAY}")
    print(f"[launcher] Python   : {python}")

    if not GATEWAY.is_file():
        print(f"[launcher] 找不到 task_agent_gateway.py：{GATEWAY}")
        return 2

    gateway_up = port_open(host, port) and http_ok(url + "backend/status", 2.0)
    comfy_up = http_ok("http://127.0.0.1:8188/system_stats", 2.0)

    if args.check:
        print(f"[launcher] 网关状态 : {'已在运行' if gateway_up else '未运行'}")
        print(f"[launcher] ComfyUI  : {'在线' if comfy_up else '未检测到 127.0.0.1:8188'}")
        if not comfy_up:
            print("[launcher] 提示：面板能打开，但打标/出图/工作流需要先启动 ComfyUI（秋叶启动器）。")
        return 0

    if gateway_up:
        print("[launcher] 网关已经在运行，直接打开面板。")
        if not args.no_browser and config.get("openBrowser", True):
            open_browser(url)
        return 0

    if port_open(host, port):
        print(f"[launcher] 端口 {port} 已被其他程序占用。改 launcher/config.local.json 的 port，或先关掉占用程序。")
        return 3

    if config.get("checkComfyUi", True) and not comfy_up:
        print("[launcher] 警告：未检测到 ComfyUI(127.0.0.1:8188)。面板能打开，但打标/出图/工作流会失败。")
        print("[launcher] 建议先启动秋叶启动器，再重新运行本启动器。")

    gateway_config = build_gateway_config(host, port)
    command = [python, "-B", str(GATEWAY)]
    if gateway_config is not None:
        command.extend(["--config", str(gateway_config)])
    print(f"[launcher] 启动命令 : {' '.join(command)}")
    try:
        process = subprocess.Popen(command, cwd=str(PACKAGE_DIR))
    except OSError as error:
        print(f"[launcher] 启动失败：{error}")
        print("[launcher] 在 launcher/config.local.json 里把 pythonPath 指向 ComfyUI 的 python.exe 再试。")
        return 4

    try:
        for _ in range(60):
            if port_open(host, port):
                break
            if process.poll() is not None:
                print(f"[launcher] 网关进程提前退出，返回码 {process.returncode}。")
                return 5
            time.sleep(0.5)
        else:
            print("[launcher] 等待网关超时。看上面的网关日志。")
            process.terminate()
            return 6

        print(f"[launcher] 已启动：{url}")
        print("[launcher] 关闭这个窗口或按 Ctrl+C 停止面板。")
        if not args.no_browser and config.get("openBrowser", True):
            open_browser(url)
        process.wait()
    except KeyboardInterrupt:
        print("\n[launcher] 正在停止网关…")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
