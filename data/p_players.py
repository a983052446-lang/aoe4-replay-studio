import sys, gzip
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay import custom
from aoereplay.config import Config
# 对局 ID 从命令行传：python data/p_players.py 123456789 234567890
GIDS = [int(x) for x in sys.argv[1:] if x.isdigit()]
if not GIDS:
    print("用法: python data/p_players.py <对局ID> [<对局ID> ...]")
    raise SystemExit(1)

cfg = Config()
for gid in GIDS:
    files = sorted(cfg.network_dir.glob(f"M_{gid}_*"), key=lambda x: x.stat().st_size, reverse=True)
    if not files: continue
    p = files[0]
    data = gzip.decompress(p.read_bytes())
    ps = custom.parse_players(data)
    print(f"=== {gid} ({p.stat().st_size//1024}KB) 解析出 {len(ps)} 名玩家")
    for x in ps:
        print(f"   {x['steam_id']}  {x['name']!r:22}  {x['civ']:<22} {x['civ_zh']}")
    print()
