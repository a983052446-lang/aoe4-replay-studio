import sys
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.server import Hub
h = Hub()
c = h.custom_games()
from collections import Counter
print("来源分布:", dict(Counter(e.get("source") for e in c["entries"])))
print("有玩家数据:", sum(1 for e in c["entries"] if e.get("players")))
print()
print("=== 前 10 条详情 ===")
for e in c["entries"][:10]:
    print(f"  {e.get('source'):<8} {str(e.get('game_id')):<12} size={e.get('size',0)//1024:>5}KB players={len(e.get('players') or [])} is_mine={e.get('is_mine')}")
