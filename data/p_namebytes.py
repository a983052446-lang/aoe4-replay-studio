# -*- coding: utf-8 -*-
"""输出每个名字槽「到 steam id」之间的原始字节，用来推断玩家区布局。

用法:
    python data/p_namebytes.py            # 自动取最新一场
    python data/p_namebytes.py 123456789  # 指定对局

不给 -n 时会自己从样本里挑前几个 UTF-16 名字当探测目标。
"""
import sys
from pathlib import Path

sys.path.insert(0, ".")
from aoereplay.config import Config

cfg = Config()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import pick  # noqa: E402

p = pick()
raw = p.read_bytes()
data = __import__("gzip").decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
fi = data.find(b"FOLDINFO", 76)
win = data[fi : fi + 12000]


def auto_names(seg: bytes, limit: int = 4) -> list[str]:
    """从样本里自己找出几个 UTF-16 名字，不用预先知道是谁。"""
    import re

    out = []
    for m in re.finditer(rb"\x01([\x02-\x28])\x00\x00\x00", seg):
        n = m.group(1)[0]
        body = seg[m.end() : m.end() + n * 2]
        if len(body) < n * 2:
            continue
        try:
            s = body.decode("utf-16-le", "ignore")
        except Exception:  # noqa: BLE001
            continue
        if s.isprintable() and len(s) >= 2 and not s.isdigit() and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


targets = sys.argv[2:] if len(sys.argv) > 2 else auto_names(win)

for name in targets:
    b = name.encode("utf-16-le")
    i = win.find(b)
    print(f"=== {name!r} @ {i}")
    if i < 0:
        print("   样本里没有这个名字")
        continue
    print("   前 24 字节:", win[max(0, i - 24) : i].hex())
