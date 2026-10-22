# -*- coding: utf-8 -*-
"""回放能不能播 —— 以游戏日志为准。

引擎里有一句权威判定（warnings.log）：

    Ignoring replay [playback:AgeIV_Replay_xxx] as it is version [26988]
    and current build is [11308]

回放文件里并没有一个能可靠读出的“版本号”字段（实测：DATASDSC/FOLDINFO 的
version 都是块格式版本，26988/11308 这类整数在文件里到处都是，无法区分）。
所以这里不做字节猜测，改成两条互补的策略：

1. **启动前预判**：读日志里历史上出现过的 `Ignoring replay ... is version [X]`，
   建立 game_id → version 的黑名单，这些直接标“版本不符”。
2. **启动后确认**：点播放后延迟读日志，出现新的 Ignoring 行就把这场标红。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

LOG = Path.home() / "Documents" / "My Games" / "Age of Empires IV" / "warnings.log"

RE_IGNORE = re.compile(
    r"Ignoring replay \[playback:AgeIV_Replay_(\d+)\] as it is version \[(\d+)\] and current build is \[(\d+)\]"
)
RE_RUNOPTS = re.compile(r"RUN-OPTIONS \[(.*?)\]")
RE_VERSION = re.compile(r"Version \[([\d.]+)\] Info \[\[([^\]]*)\]\[([^\]]*)\]\[([^\]]*)\]")


def _tail(max_bytes: int = 400_000) -> str:
    if not LOG.exists():
        return ""
    try:
        size = LOG.stat().st_size
        with open(LOG, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            return fh.read().decode("utf-8", "ignore")
    except OSError:
        return ""


_CACHE: dict[str, tuple[tuple, object]] = {}


def _cached(key: str, stamp: tuple, build):
    """按 (日志大小, mtime) 缓存解析结果。

    state() 每渲染一行就要问一次「这场被版本拒绝过没」，一页 100 行就是 100 次
    1~2 MB 的正则扫描；界面每 2.5 秒轮询一次，CPU 全耗在这上面。
    """
    hit = _CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    val = build()
    _CACHE[key] = (stamp, val)
    return val


def _log_stamp() -> tuple:
    try:
        st = LOG.stat()
        return (st.st_size, st.st_mtime)
    except OSError:
        return (0, 0.0)


def ignored_replays() -> dict[int, dict]:
    """历史上被引擎拒绝过的回放：{game_id: {"replay_version":.., "client_build":..}}"""
    def build() -> dict[int, dict]:
        out: dict[int, dict] = {}
        for m in RE_IGNORE.finditer(_tail(2_000_000)):
            gid = int(m.group(1))
            out[gid] = {"replay_version": int(m.group(2)), "client_build": int(m.group(3)), "at": time.time()}
        return out

    return _cached("ignored", _log_stamp(), build)


def client_build() -> int | None:
    """当前客户端 build（日志里最近一次打的）。"""
    tail = _tail(200_000)
    m = None
    for m2 in RE_IGNORE.finditer(tail):
        m = m2
    if m:
        return int(m.group(3))
    return None


def client_version_string() -> str | None:
    m = None
    for m2 in RE_VERSION.finditer(_tail(200_000)):
        m = m2
    return m.group(1) if m else None


def watch_after(ms: int = 6000, since_offset: int | None = None) -> dict:
    """点播放后调用：等一会儿看日志有没有新增 Ignoring 行。"""
    start = since_offset if since_offset is not None else (LOG.stat().st_size if LOG.exists() else 0)
    time.sleep(ms / 1000.0)
    if not LOG.exists():
        return {"checked": False}
    try:
        with open(LOG, "rb") as fh:
            fh.seek(min(start, LOG.stat().st_size))
            fresh = fh.read().decode("utf-8", "ignore")
    except OSError:
        return {"checked": False}
    hit = None
    for m in RE_IGNORE.finditer(fresh):
        hit = m
    if hit:
        return {
            "checked": True,
            "ignored": True,
            "game_id": int(hit.group(1)),
            "replay_version": int(hit.group(2)),
            "client_build": int(hit.group(3)),
        }
    loaded = "PlaybackSynchronization::GetSyncCommand" in fresh or "GAME -- Starting mission:" in fresh
    return {"checked": True, "ignored": False, "loaded": loaded}


def client_build_from_exe() -> int | None:
    """读 RelicCardinal.exe 的文件版本（16.3.11308.0 → 11308）。"""
    import subprocess

    for exe in (
        r"D:\Steam\steamapps\common\Age of Empires IV\RelicCardinal.exe",
        r"C:\Program Files (x86)\Steam\steamapps\common\Age of Empires IV\RelicCardinal.exe",
    ):
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"(Get-Item '{exe}').VersionInfo.FileVersion"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            m = re.search(r"(\d{3,6})", out.stdout or "")
            if m:
                return int(m.group(1))
        except Exception:  # noqa: BLE001
            continue
    return client_build()


def log_size() -> int:
    try:
        return LOG.stat().st_size if LOG.exists() else 0
    except OSError:
        return 0
