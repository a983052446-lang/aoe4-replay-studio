import sys, time, json
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay import custom, api
from aoereplay.config import Config

cfg = Config()
net = [e for e in custom.scan_network(cfg.network_dir, set()) if e["playable"]]
# 筛出自定义的
cust = []
for e in net:
    try:
        if custom.fast_custom(Path(e["path"]), cfg.network_dir):
            cust.append(e)
    except Exception:
        pass
print("自定义对局:", len(cust))

ME = cfg.profile_id
mine = other = nodata = 0
for e in cust[:30]:
    gid = e["game_id"]
    try:
        d = api.game_detail(gid)
        ids = [ (en.get("player") or en).get("profile_id") for t in (d.get("teams") or []) for en in t ]
        names = [ (en.get("player") or en).get("name") for t in (d.get("teams") or []) for en in t ]
        if ME in ids:
            mine += 1
            print(f"  {gid}  有数据·含我  players={names[:3]}")
        else:
            other += 1
            print(f"  {gid}  有数据·不含我 players={names[:3]}")
    except Exception:
        nodata += 1
    time.sleep(0.4)
print(f"\n抽样 30：含我 {mine} / 不含我 {other} / 无数据 {nodata}")
