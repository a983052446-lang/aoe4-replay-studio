# -*- coding: utf-8 -*-
r"""诊断脚本公用小工具。

以前每个 p_*.py 都写死了某个具体对局，比如：
    p = sorted(cfg.network_dir.glob("M_256805076_*"))[0]

那串数字是原作者的对局，分发出去既泄露信息，别人也跑不了。
现在统一走这里的 `pick()`：
    python data/p_who.py            → 自动取 network 里最新的一场
    python data/p_who.py 256805076  → 指定对局 ID
    python data/p_who.py D:\x\M_xxx → 直接给文件路径
"""
from __future__ import annotations

import gzip
import sys
from pathlib import Path


def _usage() -> None:
    print("用法: python data/<脚本>.py [game_id | 回放文件路径]")
    print("  不传参数 = 自动取 network 目录里最新的一场")


def pick(arg: str | None = None) -> Path:
    """拿到一个可解压的回放文件。"""
    sys.path.insert(0, ".")
    from aoereplay.config import Config

    cfg = Config()
    arg = (arg or (sys.argv[1] if len(sys.argv) > 1 else "")).strip()

    if arg:
        p = Path(arg)
        if p.exists():
            return p
        # 当成 game_id
        hits = sorted(cfg.network_dir.glob(f"M_{arg}_*"), key=lambda x: x.stat().st_size, reverse=True)
        if hits:
            return hits[0]
        pb = cfg.playback_dir / f"AgeIV_Replay_{arg}"
        if pb.exists():
            return pb
        print(f"[x] 找不到对局 {arg}")
        _usage()
        raise SystemExit(1)

    # 没传参数：取最新一场
    cands = [p for p in cfg.network_dir.glob("M_*") if p.is_file()] if cfg.network_dir.exists() else []
    if not cands:
        print(f"[x] {cfg.network_dir} 里没有回放缓存，请在游戏里点开任意一场录像再看")
        raise SystemExit(1)
    return max(cands, key=lambda p: p.stat().st_mtime)


def load(arg: str | None = None) -> tuple[Path, bytes]:
    """(路径, 解压后的字节)。"""
    p = pick(arg)
    raw = p.read_bytes()
    data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    print(f"[i] 样本: {p.name}  ({len(data)} 字节)")
    return p, data
