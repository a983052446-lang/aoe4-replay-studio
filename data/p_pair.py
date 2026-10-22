import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config

cfg = Config()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import pick
p = pick()
data = __import__('gzip').decompress(p.read_bytes()) if p.read_bytes()[:2] == b'\x1f\x8b' else p.read_bytes()
win = data[397:397+20000]

ids = [(m.start(), m.group(0).decode("utf-16-le")) for m in re.finditer(rb"(?:[\x30-\x39]\x00){9,17}", win)]
names = []
for m in re.finditer(rb"\x01([\x02-\x28])\x00\x00\x00", win):
    n = m.group(1)[0]
    body = win[m.end():m.end()+n*2]
    try:
        s = body.decode("utf-16-le")
    except Exception:
        continue
    if s.isprintable() and not s.isdigit():
        names.append((m.start(), s))

print("按「名字属于前一个 ID」配对:")
for noff, name in names:
    prev = [i for i in ids if i[0] < noff]
    nxt = [i for i in ids if i[0] > noff]
    pi = prev[-1] if prev else None
    print(f"   {name!r:24} @{noff:5}  前一个ID: {pi[1] if pi else '-':<20} 后一个ID: {nxt[0][1] if nxt else '-'}")
