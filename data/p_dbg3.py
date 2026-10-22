import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config

cfg = Config()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import pick
p = pick()
data = __import__('gzip').decompress(p.read_bytes()) if p.read_bytes()[:2] == b'\x1f\x8b' else p.read_bytes()
win = data[397:397+8000]

ms = list(re.finditer(rb"(?:[\x30-\x39]\x00){9,17}", win))
for m in ms[:4]:
    sid = m.group(0).decode("utf-16-le")
    tail = win[m.end(): m.end()+220]
    print(f"=== {sid}")
    print("  tail 前 80:", tail[:80])
    # 名字尝试
    nm = re.match(rb"((?:[\x20-\x7e]\x00){2,40})", tail)
    print("  纯ascii名字:", nm.group(1).decode("utf-16-le") if nm else None)
    # 文明
    cm = re.search(rb"([a-z][a-z0-9_]{2,20})\x00default", tail)
    print("  文明:", cm.group(1).decode() if cm else None)
    print()
