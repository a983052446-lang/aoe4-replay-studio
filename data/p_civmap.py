# -*- coding: utf-8 -*-
"""查一场排位局里每个玩家的文明 / 旗帜映射，用来对照 AoE4World 的旗帜。"""
import json
import sys
import urllib.request

PORT = sys.argv[2] if len(sys.argv) > 2 else "8788"
d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/state", timeout=60).read())
games = d.get("games") or []
print("games", len(games))
kw = sys.argv[1] if len(sys.argv) > 1 else "Danube"
for g in games:
    if kw.lower() not in (g.get("map") or "").lower():
        continue
    print(g["game_id"], g["started_local"], "|", g["map"], "|", g.get("kind"))
    def show(p, tag):
        if not p:
            return
        print("   %-5s %-12s civ=%-22s zh=%-10s icon=%s" % (
            tag, p.get("name"), p.get("civ"), p.get("civ_zh"), p.get("civ_icon")))
    show(g.get("me"), "me")
    for m in (g.get("matesRaw") or []):
        show(m, "mate")
    for o in (g.get("opponents") or []):
        show(o, "opp")
