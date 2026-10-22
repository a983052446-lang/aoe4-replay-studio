# -*- coding: utf-8 -*-
"""验证「模式是推出来的」：在样本里搜各种模式串，全零命中。

用法:
    python data/p_mode5.py                # 自动取最新一场
    python data/p_mode5.py 123456789      # 指定对局
"""
import sys
from pathlib import Path

sys.path.insert(0, ".")
from aoereplay.config import Config

cfg = Config()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import load  # noqa: E402

KEYS = [
    b"qm_4v4", b"rm_4v4", b"qm_2v2", b"rm_1v1", b"qm_1v1", b"qm_ffa",
    b"automatch", b"matchmaking", b"quickmatch", b"ranked", b"skirmish",
    b"game_type", b"gametype", b"mode_", b"victory", b"treaty",
]

_p, data = load()

print("在整份回放里搜模式相关字符串：")
any_hit = False
for k in KEYS:
    n = data.count(k)
    if n:
        any_hit = True
        print(f"   {k.decode():<14} 命中 {n} 次")
    else:
        print(f"   {k.decode():<14} 0")
print()
print("结论：", "有命中，需要重新判断" if any_hit else "全部零命中 → 模式只能按玩家数推")
