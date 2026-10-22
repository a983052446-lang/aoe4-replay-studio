import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay import custom
from aoereplay.config import Config
# 对局 ID 从命令行传：python data/p_teams.py 123456789 234567890
GIDS = [int(x) for x in sys.argv[1:] if x.isdigit()]
if not GIDS:
    print("用法: python data/p_teams.py <对局ID> [<对局ID> ...]")
    raise SystemExit(1)

cfg = Config()
for gid in GIDS:
    files = sorted(cfg.network_dir.glob(f"M_{gid}_*"), key=lambda x: x.stat().st_size, reverse=True)
    if not files: continue
    data = gzip.decompress(files[0].read_bytes())
    i = data.find(b"section_cac_conditions")
    blob = data[i:i+9000]
    # 找 "队伍,槽位)" 模式
    slots = re.findall(rb"(\d),(\d+)\)", blob)
    teams = {}
    for a, b in slots:
        teams.setdefault(int(a), []).append(int(b))
    ps = custom.parse_players(data)
    print(f"=== {gid}: 玩家 {len(ps)} 人")
    print(f"   槽位模式: {[(k, sorted(set(v))) for k,v in sorted(teams.items())]}")
    print()
