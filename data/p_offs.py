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
names = [(m.start(), m.group(0).decode("utf-16-le")) for m in re.finditer(rb"((?:[\x20-\x7e]\x00){3,40})", win)]

print("steam ids:")
for off, s in ids[:6]:
    print(f"   @{off}  {s}")
print("\nascii utf16 候选串:")
for off, s in names[:20]:
    print(f"   @{off}  {s!r}")
