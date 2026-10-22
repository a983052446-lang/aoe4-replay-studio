import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config

cfg = Config()
# 带自定义设置块的那场
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import pick
p = pick()
data = __import__('gzip').decompress(p.read_bytes()) if p.read_bytes()[:2] == b'\x1f\x8b' else p.read_bytes()
print("文件:", p.name[:44], len(data))

# 搜 mod 相关关键词
KEYS = [b"mod", b"pbgid", b"scenario", b"extension", b"pack", b"custom_mod",
        b"publisher", b"tuning", b"datapack", b"dgpack", b"signature_pack"]
for k in KEYS:
    idxs = [i for i in range(len(data)) if data.startswith(k, i)][:3]
    if idxs:
        print(f"\n{k} @ {idxs}")
        for i in idxs[:2]:
            print("   ", data[max(0,i-20):i+100])
