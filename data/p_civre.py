import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config
from aoereplay.civs import CIV_ZH

cfg = Config()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import pick
p = pick()
data = __import__('gzip').decompress(p.read_bytes()) if p.read_bytes()[:2] == b'\x1f\x8b' else p.read_bytes()
win = data[397:397+20000]

# 实际字节: 01 09 000000 "hre_ha_01" 00 00 22 00
m = re.search(rb"([a-z][a-z0-9_]{2,20})\x00", win)
print("第一个 ascii 候选:", m.group(1) if m else None)

# 我的正则
cm = re.search(rb"([a-z][a-z0-9_]{2,20})\x00\x00\x22", win)
print("正则 \\x00\\x00\\x22 匹配:", cm.group(1) if cm else None)
cm2 = re.search(rb"([a-z][a-z0-9_]{2,20})\x00", win)
print("正则 \\x00 匹配:", cm2.group(1) if cm2 else None)
print("hre_ha_01 在 CIV_ZH 吗:", "hre_ha_01" in CIV_ZH)
print("hre 在 CIV_ZH 吗:", "hre" in CIV_ZH)
