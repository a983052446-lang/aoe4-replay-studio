# -*- coding: utf-8 -*-
"""回放版本探测。

游戏启动时会对 `playback:<名字>` 做一次版本比对，不一致直接忽略：

    Ignoring replay [playback:AgeIV_Replay_xxx] as it is version [26988]
    and current build is [11308]

这条规则在引擎里，外部改不了。能做的是**提前读出来告诉用户**：
  * 回放里的 build 号（Relic Chunky 的 DATADSDSC / 场景描述块里）
  * 当前客户端 build 号（注册表/文件版本 或 游戏目录里的版本文件）
两边不一致就标红，别让你点了才发现播不了。
"""
from __future__ import annotations

import re
import struct
import subprocess
from pathlib import Path

from . import gz

MAGIC = gz.MAGIC


def _read_blocks(raw: bytes, limit: int = 64) -> list[tuple[str, int, int, int]]:
    """遍历 Relic Chunky 块：返回 [(type, version, size, data_offset)]。

    实际磁盘布局（实测）：
        "Relic Chunky\\r\\n\\x1a\\x00"  16 字节
        uint32 version                 4 字节
        uint32 ???                     4 字节
        然后是 20 字节块头：type(8) version(4) size(4) flags(4)
    """
    blocks = []
    idx = raw.find(MAGIC)
    if idx < 0:
        return blocks
    pos = idx + 16 + 4 + 4  # 魔数 + 版本 + 保留
    while pos + 20 <= len(raw) and len(blocks) < limit:
        name = raw[pos : pos + 8].rstrip(b"\x00").decode("ascii", "ignore")
        if not re.fullmatch(r"[A-Z0-9 _-]{2,8}", name or ""):
            break
        ver, size, flags = struct.unpack("<III", raw[pos + 8 : pos + 20])
        blocks.append((name, ver, size, pos + 20))
        pos = pos + 20 + size
    return blocks


def read_meta(path: Path) -> dict:
    """读出一场回放的版本/地图等元信息，读不到就给 None。"""
    try:
        raw = path.read_bytes()
    except OSError as e:
        return {"error": str(e)}
    data = gz.uncompressed(raw)
    blocks = _read_blocks(data)
    info: dict = {
        "size": len(data),
        "blocks": [b[0] for b in blocks],
        "build": None,
        "game_version": None,
        "map": None,
        "date": None,
    }
    # 日期：魔数前的 UTF-16LE 串
    idx = data.find(MAGIC)
    if idx > 12:
        try:
            info["date"] = data[12:idx].decode("utf-16-le", "ignore").strip("\x00").strip()
        except Exception:  # noqa: BLE001
            pass
    # 在 DATASDSC / 场景描述里找版本与地图
    for name, ver, size, off in blocks:
        chunk = data[off : off + size]
        text = chunk.decode("latin-1", "ignore")
        if name.startswith("DATA"):
            for pat, key in (
                (r"build[_ ]?(?:num|version)?[\"']?\s*[:=]\s*(\d{3,6})", "build"),
                (r"version[\"']?\s*[:=]\s*(\d{3,6})", "game_version"),
                (r"(?:map|scenario)[\"']?\s*[:=]\s*[\"']?([a-z0-9_]{3,40})", "map"),
            ):
                m = re.search(pat, text, re.I)
                if m:
                    info[key] = m.group(1)
        if info.get("build") and info.get("map"):
            break
    return info


def game_build() -> int | None:
    """当前客户端的 build 号：优先读注册表/文件版本，退化为日志里的 CRC 行。"""
    for exe in (
        Path(r"D:\Steam\steamapps\common\Age of Empires IV\RelicCardinal.exe"),
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\Age of Empires IV\RelicCardinal.exe"),
    ):
        if not exe.exists():
            continue
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Item '{exe}').VersionInfo.FileVersion"],
                capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            m = re.search(r"(\d{3,6})", r.stdout or "")
            if m:
                return int(m.group(1))
        except Exception:  # noqa: BLE001
            pass
    # 退化：从 warnings.log 的 "current build is [11308]" 里捞
    log = Path.home() / "Documents" / "My Games" / "Age of Empires IV" / "warnings.log"
    if log.exists():
        try:
            tail = log.read_text(encoding="utf-8", errors="ignore")[-8000:]
            m = re.search(r"current build is \[(\d{3,6})\]", tail)
            if m:
                return int(m.group(1))
        except OSError:
            pass
    return None


def check(path: Path, current: int | None = None) -> dict:
    """返回 {"build": 回放版本, "current": 客户端版本, "playable": bool, "note": str}"""
    meta = read_meta(path)
    build = meta.get("build")
    cur = current if current is not None else game_build()
    if build is None or cur is None:
        return {"build": build, "current": cur, "playable": None, "note": "版本号读不出来，只能试播", "meta": meta}
    ok = int(build) == int(cur)
    return {
        "build": int(build),
        "current": int(cur),
        "playable": ok,
        "note": "版本一致" if ok else f"回放 {build} ≠ 客户端 {cur}，游戏会忽略",
        "meta": meta,
    }
