import sys, gzip, re, hashlib
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config

cfg = Config()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import pick
p = pick()
data = __import__('gzip').decompress(p.read_bytes()) if p.read_bytes()[:2] == b'\x1f\x8b' else p.read_bytes()

# 1) 搜已知 mod 的 sga 哈希（那个哈希在文件名里）
base = Path.home() / "Documents/My Games/Age of Empires IV/mods/extension/subscriptions"
modhashes = {}
for mid in ("166901","167634","207587","207675","314963","72188"):
    d = base / mid
    if d.exists():
        f = list(d.glob("*.sga"))
        if f:
            modhashes[mid] = f[0].stem
print("已订阅 mod 的 sga 哈希:")
for k, v in modhashes.items():
    print(f"   {k}: {v[:32]}...")

# 2) 回放里有没有这些哈希
print("\n回放里搜这些哈希（前 16 字符）:")
for mid, h in modhashes.items():
    short = h[:16].encode()
    if short in data:
        print(f"   ✓ 找到 {mid} ({short.decode()})")
    else:
        print(f"   ✗ {mid}")

# 3) 搜 pbgid / 16进制标识
print("\n搜 pbgid 相关:")
for kw in (b"pbgid", b"PBGID", b"extension_id", b"modid", b"mod_id"):
    i = data.find(kw)
    if i >= 0:
        print(f"   {kw} @ {i}: {data[i:i+60]}")
