# -*- coding: utf-8 -*-
"""看某个 Steam ID 有没有落在玩家区窗口里（定位名字/ID 布局用）。

用法:
    python data/p_win.py 7656119xxxxxxxxxx
    python data/p_win.py            # 自动取样本里第一个 17 位串
"""
import re
import sys

sys.path.insert(0, ".")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import load  # noqa: E402

_p, data = load()

fi = data.find(b"FOLDINFO", 76)
win = data[fi : fi + 20000]
print("FOLDINFO @", fi, " 窗口长度", len(win))

sid = sys.argv[1].strip() if len(sys.argv) > 1 else ""
if not sid:
    m = re.search(rb"(?<!\d)7656119\d{10}(?!\d)", data)
    if not m:
        print("样本里找不到 17 位 Steam ID，请指定")
        raise SystemExit(1)
    sid = m.group(0).decode()

for enc in ("utf-16-le", "utf-8"):
    try:
        b = sid.encode(enc)
    except UnicodeEncodeError:
        continue
    print(f"{enc:10} 在窗口里 @ {win.find(b)}")
