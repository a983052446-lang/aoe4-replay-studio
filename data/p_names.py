import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config

cfg = Config()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import pick
p = pick()
data = __import__('gzip').decompress(p.read_bytes()) if p.read_bytes()[:2] == b'\x1f\x8b' else p.read_bytes()
print("文件:", p.name[:40], len(data))

# 全文搜 UTF-16LE 形式的中文/英文名字（玩家名很可能是 UTF-16）
def find_utf16(data):
    out = []
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){3,30}", data):
        try:
            s = m.group(0).decode("utf-16-le", "ignore")
            if s.strip() and not s.startswith("http"):
                out.append((m.start(), s))
        except Exception:
            pass
    return out

u16 = find_utf16(data[:500000])
print(f"\nUTF-16 字符串前 30 个（共 {len(u16)}）:")
for off, s in u16[:30]:
    print(f"   @{off}: {s}")
