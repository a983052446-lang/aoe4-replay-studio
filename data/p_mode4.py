import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config

cfg = Config()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import pick
p = pick()
data = __import__('gzip').decompress(p.read_bytes()) if p.read_bytes()[:2] == b'\x1f\x8b' else p.read_bytes()

# 设置块之后的所有字符串
i = data.find(b"section_cac_conditions")
print("设置块 @", i)
blob = data[i:i+6000]
strs = re.findall(rb"[\x20-\x7e]{4,50}", blob)
print(f"\n设置区字符串（{len(strs)} 个）:")
for s in strs[:60]:
    print("   ", s.decode("ascii","ignore"))
