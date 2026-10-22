import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config
# 对局 ID 从命令行传：python data/p_hash.py 123456789 234567890
GIDS = [int(x) for x in sys.argv[1:] if x.isdigit()]
if not GIDS:
    print("用法: python data/p_hash.py <对局ID> [<对局ID> ...]")
    raise SystemExit(1)

cfg = Config()
TARGET = "483db8a9959441839cb441ebb025b6f6"

# 1) 在所有回放里找这个 hash，看是不是每场都有
print("=== 在各回放里搜这个 hash ===")
for gid in GIDS:
    files = sorted(cfg.network_dir.glob(f"M_{gid}_*"), key=lambda x: x.stat().st_size, reverse=True)
    if not files: continue
    data = gzip.decompress(files[0].read_bytes())
    has = TARGET.encode() in data
    # 具体的 id
    m = re.search(TARGET.encode() + rb":(\d+)", data)
    print(f"   {gid}: {'有' if has else '无':<2}  id={m.group(1).decode() if m else '-'}")

# 2) 在 mod 的 sga 里搜这个 hash
print("\n=== 在 mod sga 内容里搜 ===")
base = Path.home() / "Documents/My Games/Age of Empires IV/mods/extension/subscriptions"
for mid in ("72188","314963","207587","166901"):
    d = base / mid
    if not d.exists(): continue
    f = list(d.glob("*.sga"))
    if not f: continue
    blob = f[0].read_bytes()
    print(f"   {mid}: {'命中' if TARGET.encode() in blob else '无'}")
