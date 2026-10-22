import sys, gzip
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config

cfg = Config()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import pick
p = pick()
data = __import__('gzip').decompress(p.read_bytes()) if p.read_bytes()[:2] == b'\x1f\x8b' else p.read_bytes()

# 看 500-3000 这一段的原始字节
seg = data[500:3000]
print("长度:", len(seg))
print(seg.decode("utf-16-le", "replace")[:1500])
