# -*- coding: utf-8 -*-
"""看某个 Steam ID 在回放里以什么形态存放（UTF-16 / GBK / 明文）。

用法:
    python data/p_hex.py 7656119xxxxxxxxxx
    python data/p_hex.py                # 不给就用样本里第一个 17 位数字串
"""
import re
import sys

sys.path.insert(0, ".")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import load  # noqa: E402

_p, data = load()

sid = sys.argv[1].strip() if len(sys.argv) > 1 else ""
if not sid:
    m = re.search(rb"(?<!\d)7656119\d{10}(?!\d)", data)
    if not m:
        print("样本里找不到 17 位 Steam ID，请在命令行指定一个")
        raise SystemExit(1)
    sid = m.group(0).decode()
    print(f"[i] 自动取到 {sid}")

print(f"\n查找 {sid}：")
for enc in ("utf-16-le", "utf-8", "ascii"):
    try:
        b = sid.encode(enc)
    except UnicodeEncodeError:
        continue
    i = data.find(b)
    print(f"   {enc:10} 首次出现 @ {i}" + (f"  前8字节={data[max(0,i-8):i].hex()}" if i >= 0 else ""))
