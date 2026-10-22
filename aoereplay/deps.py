# -*- coding: utf-8 -*-
r"""第三方依赖检测与自动安装。

设计目标：**别人克隆下来双击就能用，不用先自己 pip install。**

规则：
  * 核心功能只需要标准库 —— requests / websockets 都是可选增强
  * 缺了不报错，照常启动，只是对应功能降级
  * 但会主动提示，并给出一键安装（自检.bat 会调用 install_optional()）

装包来源优先级：
  1. 已经能 import → 什么都不做
  2. pip 在线装（默认走官方源，慢的话可以设环境变量 AOE4_PIP_INDEX 换清华源）
  3. 装不上 → 打印手动命令 + 不影响启动
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys

# 包名 → (import 名, 用途说明, 是否必需)
OPTIONAL = {
    "requests": ("requests", "AoE4World 数据接口 / 回放下载", False),
    "websockets": ("websockets", "浏览器面板与操作录制（CDP）", False),
}

MIRRORS = {
    "official": "https://pypi.org/simple",
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple",
}

PYTHON_DOWNLOAD = "https://www.python.org/downloads/"


def has(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except Exception:  # noqa: BLE001
        return False


def missing() -> dict[str, str]:
    """返回 {包名: 用途}，只列 import 失败的。"""
    out = {}
    for pkg, (mod, why, _required) in OPTIONAL.items():
        if not has(mod):
            out[pkg] = why
    return out


def status() -> dict:
    """给界面 / 自检用的状态快照。"""
    mp = missing()
    return {
        "python": sys.version.split()[0],
        "python_ok": sys.version_info >= (3, 10),
        "python_download": PYTHON_DOWNLOAD,
        "missing": mp,
        "all_ok": not mp,
        "features_lost": [OPTIONAL[p][1] for p in mp],
    }


def _pip_cmd(pkg: str) -> list[str]:
    index = os.environ.get("AOE4_PIP_INDEX") or MIRRORS["official"]
    return [
        sys.executable, "-m", "pip", "install", "--upgrade", "--no-warn-script-location",
        "-i", index, pkg,
    ]


def install_optional(packages: list[str] | None = None, quiet: bool = False) -> dict:
    """尝试安装缺失的可选包。返回 {installed: [], failed: {包: 原因}}。"""
    todo = packages or list(missing())
    installed, failed = [], {}
    for pkg in todo:
        if has(OPTIONAL.get(pkg, (pkg,))[0]):
            installed.append(pkg)
            continue
        cmd = _pip_cmd(pkg)
        try:
            if not quiet:
                print(f"[deps] 正在安装 {pkg} …（源：{cmd[cmd.index('-i') + 1]}）", flush=True)
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            ok = r.returncode == 0 and has(OPTIONAL[pkg][0])
            if ok:
                installed.append(pkg)
                if not quiet:
                    print(f"[deps] {pkg} 安装成功", flush=True)
            else:
                failed[pkg] = (r.stderr or r.stdout or "pip 返回非零").strip().splitlines()[-1][:160]
        except Exception as e:  # noqa: BLE001
            failed[pkg] = f"{type(e).__name__}: {e}"[:160]
    return {"installed": installed, "failed": failed}


def report() -> None:
    """启动时打印一次依赖状态，缺什么、去哪装、装不上会损失什么。"""
    st = status()
    if not st["python_ok"]:
        print()
        print(f"[!] 当前 Python {st['python']} 太旧，需要 3.10 或更新版本")
        print(f"    下载：{PYTHON_DOWNLOAD}")
        print()
    if st["all_ok"]:
        return
    print()
    print("[!] 缺少可选的 Python 包（不影响启动，但对应功能不可用）：")
    for pkg, why in st["missing"].items():
        print(f"    - {pkg:<12} → {why}")
    print()
    print("    一键安装：双击 安装依赖.bat，或手动执行：")
    print(f'      "{sys.executable}" -m pip install -r requirements.txt')
    if not has("pip"):
        print("    （pip 也没有？先跑： python -m ensurepip --upgrade ）")
    print()
