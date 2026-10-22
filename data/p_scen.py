import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config
# 对局 ID 从命令行传：python data/p_scen.py 123456789 234567890
GIDS = [int(x) for x in sys.argv[1:] if x.isdigit()]
if not GIDS:
    print("用法: python data/p_scen.py <对局ID> [<对局ID> ...]")
    raise SystemExit(1)

cfg = Config()
for gid in GIDS:
    files = sorted(cfg.network_dir.glob(f"M_{gid}_*"), key=lambda x: x.stat().st_size, reverse=True)
    if not files: continue
    data = gzip.decompress(files[0].read_bytes())
    # 找 data:scenarios\... 或 scenarios\\
    paths = set()
    for m in re.finditer(rb"data:scenarios[\\/][\x20-\x7e]{3,80}", data):
        paths.add(m.group(0).decode("ascii","ignore"))
    for m in re.finditer(rb"scenarios[\\/][a-z0-9_\\/]{3,60}", data):
        paths.add(m.group(0).decode("ascii","ignore"))
    print(f"=== {gid}")
    for p in list(paths)[:6]:
        print(f"   {p}")
    print()
