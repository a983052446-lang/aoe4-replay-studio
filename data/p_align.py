import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config

cfg = Config()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import pick
p = pick()
data = __import__('gzip').decompress(p.read_bytes()) if p.read_bytes()[:2] == b'\x1f\x8b' else p.read_bytes()
fi = data.find(b"FOLDINFO", 76)
win = data[fi:fi+12000]

ids = [(m.start(), m.group(0).decode("utf-16-le")) for m in re.finditer(rb"(?:[\x30-\x39]\x00){9,17}", win)]
# 名字 = 非数字的 utf16 串，长度>=2，排除纯符号
cands = []
for m in re.finditer(rb"((?:[\x20-\x7e]\x00){2,40})", win):
    s = m.group(0).decode("utf-16-le")
    if s.isdigit(): continue
    if len(s.strip()) < 2: continue
    cands.append((m.start(), s))

print("IDs:", len(ids))
print("名字候选:", len(cands))
print()
print("按顺序对照:")
for i in range(max(len(ids), len(cands))):
    a = ids[i] if i < len(ids) else ("-", "-")
    b = cands[i] if i < len(cands) else ("-", "-")
    print(f"  {i}: id@{a[0]} {a[1]:<18} | name@{b[0]} {b[1]!r}")
