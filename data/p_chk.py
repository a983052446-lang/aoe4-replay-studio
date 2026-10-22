import sys, json
sys.path.insert(0, ".")
from aoereplay.server import Hub
h = Hub()
c = h.custom_games()
for e in c["entries"][:3]:
    print(f"game_id={e.get('game_id')} kind={e.get('kind')!r} map={e.get('map')!r}")
    print(f"  cls={e.get('cls')} api={bool(e.get('api'))} players={len(e.get('players') or [])}")
    for p in (e.get("players") or [])[:3]:
        print(f"    player: name={p.get('name')!r} civ={p.get('civ')!r} civ_zh={p.get('civ_zh')!r}")
