import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config
# 对局 ID 从命令行传：python data/p_refs.py 123456789 234567890
GIDS = [int(x) for x in sys.argv[1:] if x.isdigit()]
if not GIDS:
    print("用法: python data/p_refs.py <对局ID> [<对局ID> ...]")
    raise SystemExit(1)

cfg = Config()
for gid in GIDS:
    files = sorted(cfg.network_dir.glob(f"M_{gid}_*"), key=lambda x: x.stat().st_size, reverse=True)
    if not files: continue
    data = gzip.decompress(files[0].read_bytes())
    print(f"=== {gid} ({len(data)//1024}KB)")
    # 所有 data: 开头的资源路径
    refs = set(re.findall(rb"data:[\\/][^\x00\"'\s]{4,90}", data))
    if refs:
        for r in list(refs)[:12]:
            print("   ", r.decode("ascii","ignore"))
    else:
        print("   无 data: 引用")
    # 有没有 .lua / .xs 脚本引用
    scripts = set(re.findall(rb"[a-z0-9_\\/]{4,60}\.(lua|xs|xml)", data))
    if scripts:
        print("   脚本/配置引用:", [s.decode("ascii","ignore") for s in list(scripts)[:8]])
    print()
