# -*- coding: utf-8 -*-
"""浏览器配置初始化。

日常 Chrome 是加密保存 cookie 的；把日常配置整个复制过去，新配置里读不到
密钥就会全部退出登录。所以这里的策略是：
  * 只复制「登录状态无关」的部分（本地存储、偏好、书签、扩展）
  * 关键登录态交给用户在新窗口里点一次「用 Steam 登录」（之后一直复用）

如果检测到没人登录过（Cookies 里没有 aoe4world），界面会提示。
"""
from __future__ import annotations

import shutil
from pathlib import Path

SAFE_ITEMS = ["Local Storage", "Session Storage", "Preferences", "Bookmarks", "History", "Favicons", "Extensions"]


def seed_profile(target: Path, source: Path | None = None) -> dict:
    """把日常浏览器的非敏感数据搬进独立配置，尽量少登录几次。"""
    target.mkdir(parents=True, exist_ok=True)
    default = target / "Default"
    default.mkdir(exist_ok=True)
    report = {"copied": [], "skipped": []}
    if source is None or not source.exists():
        return report
    src = source / "Default"
    if not src.exists():
        return report
    for item in SAFE_ITEMS:
        s, d = src / item, default / item
        if not s.exists() or d.exists():
            continue
        try:
            if s.is_dir():
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
            report["copied"].append(item)
        except OSError:
            report["skipped"].append(item)
    return report


def profile_ready(profile: Path) -> bool:
    """这个独立配置里是不是已经登录过（有真实的 cookie 库）。

    只判断 Default/ 存不存在没有意义 —— seed_profile 和 actor.start 都会先把它建出来，
    于是「还没登录」这个提示永远不会出现。Chrome 把 cookie 存在
    Default/Network/Cookies（新版本）或 Default/Cookies（老版本），空库也有几百字节。
    """
    for rel in ("Default/Network/Cookies", "Default/Cookies"):
        p = profile / rel
        try:
            if p.is_file() and p.stat().st_size > 1024:
                return True
        except OSError:
            continue
    return False
