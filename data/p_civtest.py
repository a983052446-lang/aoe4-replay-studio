import sys, gzip
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay import custom
from aoereplay.config import Config
# 对局 ID 从命令行传：python data/p_civtest.py 123456789 234567890
GIDS = [int(x) for x in sys.argv[1:] if x.isdigit()]
if not GIDS:
    print("用法: python data/p_civtest.py <对局ID> [<对局ID> ...]")
    raise SystemExit(1)

cfg = Config()
for gid in GIDS:
    files = sorted(cfg.network_dir.glob(f"M_{gid}_*"), key=lambda x: x.stat().st_size, reverse=True)
    if not files: continue
    data = gzip.decompress(files[0].read_bytes())
    ps = custom.parse_players(data)
    got = sum(1 for p in ps if p["civ_zh"])
    print(f"=== {gid}: {len(ps)} 人，有文明 {got} 人")
    for p in ps:
        print(f"   {p['name']!r:24} {p['civ']:<14} {p['civ_zh']}")
    print()
