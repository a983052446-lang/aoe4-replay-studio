import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config

cfg = Config()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import pick
p = pick()
data = __import__('gzip').decompress(p.read_bytes()) if p.read_bytes()[:2] == b'\x1f\x8b' else p.read_bytes()

fi = data.find(b"FOLDINFO")
print("FOLDINFO @", fi)
seg = data[fi:fi+3000]
strs = re.findall(rb"[\x20-\x7e]{2,40}", seg)
print("\nFOLDINFO 区域字符串（前 50）:")
for s in strs[:50]:
    print("   ", s.decode("ascii","ignore"))
