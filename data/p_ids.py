import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config

cfg = Config()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import pick
p = pick()
data = __import__('gzip').decompress(p.read_bytes()) if p.read_bytes()[:2] == b'\x1f\x8b' else p.read_bytes()

# 把所有 "16进制:数字" 形式的标识符列出来（场景/pack 引用常见形式）
ids = set(re.findall(rb"([0-9a-f]{16,64}):(\d{5,12})", data[:200000]))
print(f"=== 形如 <hash>:<id> 的引用 {len(ids)} 个 ===")
for h, n in list(ids)[:12]:
    print(f"   {h.decode()}:{n.decode()}")

# 再看看 FOLDINFO 块之后、玩家列表之前的区域有什么
fi = data.find(b"FOLDINFO")
print(f"\n=== FOLDINFO @ {fi} 前 200 字节 ===")
print(data[max(0,fi-200):fi])
