# -*- coding: utf-8 -*-
"""Relic 回放 gzip 包裹层。

游戏云同步下来的录像（*.gz，以及 playback 里 M_<gameid>_<hash> 这种）在
"Relic Chunky" 魔数前面还有一段头部 + zlib 头 78 9C。
游戏自己读的时候会跳过这段，所以解包成裸流再喂给 -replay 最稳；
要还原成云同步原名，用 wrap() 把头部塞回去。
"""
from __future__ import annotations

import gzip
import os
import re
import zlib
from pathlib import Path

MAGIC = b"Relic Chunky"
# 游戏 playback\ 里实际存的格式："," 前缀的 AOE4_RE 包裹头
AOE4_HEADER = b"\x00\x00,,\x41\x4f\x45\x34\x5f\x52\x45"
ZLIB_HEADERS = (b"\x78\x9c", b"\x78\x01", b"\x78\xda")
NAME_RE = re.compile(r"^M_(\d+)_([0-9a-fA-F]{64})\.gz$")


def to_playback(raw: bytes) -> bytes:
    """云端下载的字节 → 直接能放进 playback\\ 的字节。

    实测（2026-10-22）：
        云端原始 = gzip( ",,AOE4_RE + 日期(UTF-16) + zlib(Relic Chunky 块流)" )
        游戏自己存 = 解开最外层 gzip，保留 ",,AOE4_RE" 包裹
    所以只解**一层** gzip，别再往里剥 —— 再剥就丢了版本头，
    引擎会报 `Ignoring replay ... as it is version [26988]`。
    """
    if raw[:2] == b"\x1f\x8b":
        data = gzip.decompress(raw)
    else:
        data = raw
    return data


def is_playback_format(data: bytes) -> bool:
    """是不是游戏 playback\\ 目录认的格式。"""
    if data.startswith(AOE4_HEADER):
        return True
    if data[:12] == MAGIC:          # 兼容个别裸流（游戏也能读，但没有版本头）
        return True
    return False


def uncompressed(data: bytes) -> bytes:
    """去掉 Relic 包裹层，返回 Relic Chunky 裸流。不认识的格式原样返回。"""
    if not data:
        return data
    # 1) 已经是裸 Relic Chunky
    if data[:12] == MAGIC:
        return data
    # 2) 纯 gzip（1f 8b）
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except Exception:
            return data
    # 3) 包裹层：找 zlib 头，从那里解压
    for hdr in ZLIB_HEADERS:
        idx = data.find(hdr)
        while idx != -1:
            try:
                out = zlib.decompress(data[idx:])
                if out[:12] == MAGIC or len(out) > 4096:
                    return out
            except Exception:
                pass
            idx = data.find(hdr, idx + 1)
    # 4) 干脆里面有 Relic Chunky
    idx = data.find(MAGIC)
    if idx > 0:
        return data[idx:]
    return data


def wrap(raw: bytes, saved: bytes | None = None) -> bytes:
    """把裸流塞回原始包裹层（沿用原文件头部，保证与游戏云端件逐字节一致）。"""
    if saved is None:
        return raw
    for hdr in ZLIB_HEADERS:
        idx = saved.find(hdr)
        if idx > 0:
            try:
                if zlib.decompress(saved[idx:])[:12] == MAGIC:
                    head = saved[:idx]
                    return head + zlib.compress(raw, 9)
            except Exception:
                continue
    return raw


def parse_name(path: str | os.PathLike) -> tuple[bool, int | None, str | None]:
    """返回 (是否原始云同步格式, game_id, 64位哈希)。"""
    name = Path(path).name
    m = NAME_RE.match(name)
    if m:
        return True, int(m.group(1)), m.group(2).lower()
    return False, None, None


def sniff(path: Path) -> dict:
    """轻量嗅探文件格式，给界面显示用。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
    except OSError:
        return {"format": "unknown"}
    if head[:12] == MAGIC:
        return {"format": "chunky"}
    for hdr in ZLIB_HEADERS:
        if hdr in head[:24]:
            return {"format": "wrapped-gz"}
    if head[:2] == b"\x1f\x8b":
        return {"format": "gzip"}
    return {"format": "other"}
