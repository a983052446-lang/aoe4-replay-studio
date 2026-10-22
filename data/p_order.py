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
win = data[fi:fi+20000]

# 打印所有 id 和所有名字的偏移，看顺序
ids = [(m.start(), m.group(0).decode("utf-16-le")) for m in re.finditer(rb"(?:[\x30-\x39]\x00){9,17}", win)]
print("ID 偏移:")
for off, s in ids:
    print(f"   @{off}  {s}")
print()
# 名字：01 <len> 000000 + utf16
print("名字偏移:")
for m in re.finditer(rb"\x01([\x02-\x28])\x00\x00\x00", win):
    n = m.group(1)[0]
    body = win[m.end():m.end()+n*2]
    try:
        s = body.decode("utf-16-le")
    except Exception:
        continue
    if s.isprintable() and not s.isdigit():
        print(f"   @{m.start()}  {s!r}")
